"""decrypt(): an encrypted file written anew without encryption.

open() reads an encrypted file as it is and decrypts what it reads
(docs/conventions.md, "Decryption"). decrypt() has QPDF write the file
without encryption and checks the result against the input through both
engines; the caller opens those bytes.
"""

from __future__ import annotations

import dataclasses
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pdfacts.backends import qpdf
from pdfacts.backends.protocols import (
    AnnotationView,
    DecryptedOtherwise,
    EncryptionEntries,
    EngineFailure,
    FailureKind,
    PageText,
)
from pdfacts.core import runtime
from pdfacts.core.bitmap import Colour
from pdfacts.core.document import Document, FeatureAccess, _deadline, classified, open
from pdfacts.core.errors import (
    DamagedFileError,
    DecryptionFailedError,
    EngineError,
    InputError,
    LimitExceededError,
    NotEncryptedError,
    PasswordRequiredError,
)
from pdfacts.core.geometry import PageGeometry
from pdfacts.core.limits import Limits
from pdfacts.core.values import Unknown

# The most pixels the check renders a page at, as Page.render counts them: at
# scale 1 where the page is at most this many, at a lower scale otherwise.
CHECK_PIXELS = 1 << 22
# The /CFM of a crypt filter that both engines decrypt alike, whatever /R says
# (measured).
METHODS = frozenset({b"V2", b"AESV2", b"AESV3"})
# Why QPDF and PDFium decrypt an object otherwise, by DecryptedOtherwise.kind.
OTHERWISE = {
    "crypt filter": "a stream whose /DecodeParms name a crypt filter, which QPDF takes and "
    "PDFium does not",
    "cross-reference": "a stream /Type /XRef that the document refers to, which QPDF leaves "
    "as written and PDFium decrypts",
    "signature": "a dictionary PDFium takes for a signature and QPDF does not, whose "
    "/Contents PDFium leaves as written and QPDF decrypts",
    "metadata": "the catalog's /Metadata under /EncryptMetadata false, not a stream /Type "
    "/Metadata with /Subtype /XML at /V 4 or more, which PDFium leaves as written and QPDF "
    "decrypts",
    "trailer": "a string in it, which QPDF leaves as written and PDFium decrypts",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Decryption:
    """An encrypted file written anew without encryption, and checked."""

    # The decrypted file.
    data: bytes = field(repr=False)
    # Pages in the decrypted file, as many as in the input.
    page_count: int


def decrypt(
    data: bytes,
    *,
    password: str | None = None,
    limits: Limits | None = None,
    _clock: Callable[[], float] = time.monotonic,
) -> Decryption:
    """The encrypted file `data`, written anew by QPDF without encryption and
    checked against the input.

    The input is opened as open() opens it, with its errors and its limits,
    a page of it damaged too: a password is needed where open() needs one,
    and either of the file's passwords decrypts it. Then, in this order:
    DecryptionFailedError when every value from QPDF is unknown for the
    input, whether or not it is encrypted; NotEncryptedError when both
    engines read the file as not encrypted, unless QPDF finds an encryption
    dictionary the catalog does not reach or warns that it mis-read an
    object, which is DecryptionFailedError; DecryptionFailedError when a
    crypt filter that /StmF, /StrF or /EFF names is neither absent, nor
    /Identity where /CF defines no filter of that name, nor defined in /CF
    with a /CFM of METHODS; and DecryptionFailedError when an object reached
    from the catalog or /Info is one the engines decrypt otherwise
    (OTHERWISE).

    DecryptionFailedError also when QPDF cannot write the file or reports
    anything while it reads or writes it, and when the result fails a check.
    The result has to open as open() opens a file, read as not encrypted by
    both engines, with /Size in its trailer, and give for each page what the
    input gives: its geometry, what PDFium reads in each annotation
    dictionary of its /Annots, the characters PDFium reads in its content,
    and the pixels of PDFium's rendering in RGB (CHECK_PIXELS).
    Neither the input nor the result may give a warning.

    The limits apply as for open(), to the input and to the result, with two
    exceptions: max_bytes applies to the input only, and deadline_seconds
    counts from the start of decrypt(), checked as open() checks it, once
    the file is written and after each page of the check. max_render_pixels
    applies to the renderings of the check; max_items_per_page holds the
    entries of each page's /Annots as PDFium counts them, and its characters
    as glyphs() holds them. EngineError when an engine fails otherwise.

    The object numbers of the result may differ from those of the input. Its
    /ID keeps the first part of the input's and gets a second made from its
    content. A signature of the input no longer matches the bytes.

    `_clock` is for tests.
    """
    started = _clock()
    limits = classified(data, password, limits)
    deadline = _deadline(limits.deadline_seconds, started, _clock)
    with open(data, password=password, limits=limits) as source:
        _check_encrypted(source)
        # The runtime started in open().
        with runtime.engine():
            try:
                written = qpdf.rewrite_decrypted(data, password)
            except EngineFailure as failure:
                raise _decryption_error(failure) from failure
        deadline()
        if written.warnings:
            raise DecryptionFailedError(
                detail=f"QPDF reported while it decrypted the file: {'; '.join(written.warnings)}"
            )
        _verify(source, written.data, dataclasses.replace(limits, max_bytes=None), deadline)
        return Decryption(data=written.data, page_count=source.page_count)


def _check_encrypted(source: Document) -> None:
    """open() has compared the encryption the engines read ("Object identity")."""
    with source._access() as access:
        if access.qpdf_unknown is not None:
            raise DecryptionFailedError(
                detail=f"every value from QPDF is unknown: {access.qpdf_unknown.detail}"
            )
        if access.security_handler_revision() is not None:
            refused = _refused_filter(access.encryption_entries())
            if refused is not None:
                raise DecryptionFailedError(
                    detail="its crypt filters are not all ones both engines decrypt alike: "
                    f"{refused}"
                )
            otherwise = access.decrypted_otherwise()
            if otherwise:
                raise DecryptionFailedError(detail=_otherwise(otherwise[0]))
            return
        if access.lost_encryption():
            raise DecryptionFailedError(
                detail="neither engine reads the file as encrypted, and it holds an "
                "encryption dictionary that no trailer names: its strings and streams may be "
                "encrypted with nothing to decrypt them"
            )
        if access.misread():
            raise DecryptionFailedError(
                detail="neither engine reads the file as encrypted, and QPDF warns that it "
                "mis-read an object, which may be the encryption dictionary"
            )
        raise NotEncryptedError()


def _otherwise(found: DecryptedOtherwise) -> str:
    if found.objgen is None:
        where = "an object the trailer holds"
    else:
        number, generation = found.objgen
        where = f"object {number} {generation}"
    return f"the engines decrypt {where} otherwise: {OTHERWISE[found.kind]}"


def _refused_filter(entries: EncryptionEntries) -> str | None:
    """What makes a crypt filter one the engines may decrypt otherwise; None
    where each that /StmF, /StrF and /EFF name is one they decrypt alike."""
    if isinstance(entries.crypt_filters, str):
        return entries.crypt_filters
    methods = {crypt.name: crypt.method for crypt in entries.crypt_filters}
    named = (
        ("/StmF", entries.stream_filter),
        ("/StrF", entries.string_filter),
        ("/EFF", entries.file_filter),
    )
    for key, name in named:
        if name is None:
            continue
        if isinstance(name, str):
            return name
        if name == b"Identity":
            if name in methods:
                return f"{key} names /Identity, and /CF defines a crypt filter of that name"
            continue
        if name not in methods:
            return f"{key} names a crypt filter /CF does not define"
        method = methods[name]
        if isinstance(method, str):
            return method
        if method not in METHODS:
            if method is None:
                return f"the crypt filter {key} names has no /CFM"
            return f"the crypt filter {key} names has a /CFM other than /V2, /AESV2 and /AESV3"
    return None


def _verify(source: Document, data: bytes, limits: Limits, deadline: Callable[[], None]) -> None:
    try:
        result = open(data, limits=limits)
    except (InputError, PasswordRequiredError) as error:
        # An EngineError passes: it says nothing about the result.
        raise DecryptionFailedError(detail=f"the decrypted file does not open: {error}") from error
    with result:
        with result._access() as access:
            revision = access.security_handler_revision()
        if revision is not None:
            raise DecryptionFailedError(
                detail=f"PDFium reads the decrypted file as encrypted, revision {revision}"
            )
        if result.page_count != source.page_count:
            raise DecryptionFailedError(
                detail=f"the decrypted file has {result.page_count} pages, "
                f"the file {source.page_count}"
            )
        for index in range(source.page_count):
            _compare_page(source, result, index)
            deadline()
        # Last: reading a page can add one.
        for document, which in ((source, "the file"), (result, "the decrypted file")):
            if document.warnings:
                raise DecryptionFailedError(
                    detail=f"{which} gives warnings: {'; '.join(document.warnings)}"
                )
        object_count = result.object_count
        if isinstance(object_count, Unknown):
            raise DecryptionFailedError(
                detail=f"the decrypted file has no usable /Size: {object_count.detail}"
            )


def _compare_page(source: Document, result: Document, index: int) -> None:
    before = source.pages[index]
    try:
        after = result.pages[index]
    except DamagedFileError as error:
        raise DecryptionFailedError(
            detail=f"page {index} of the decrypted file is damaged: {error}"
        ) from error
    if after.geometry != before.geometry:
        raise DecryptionFailedError(
            detail=f"page {index} of the decrypted file has the geometry {after.geometry}, "
            f"the file {before.geometry}"
        )
    with source._access() as access:
        views = _annotation_views(access, index)
        text = _page_text(access, index)
    with result._access() as access:
        if _annotation_views(access, index) != views:
            raise DecryptionFailedError(
                detail=f"PDFium reads the annotations of page {index} otherwise in the "
                "decrypted file"
            )
        if _page_text(access, index) != text:
            raise DecryptionFailedError(
                detail=f"PDFium reads the text of page {index} otherwise in the decrypted file"
            )
    # At most CHECK_PIXELS pixels, each side far below what PDFium addresses.
    scale = _scale(before.geometry)
    if after.render(scale, colour=Colour.RGB) != before.render(scale, colour=Colour.RGB):
        raise DecryptionFailedError(
            detail=f"PDFium renders page {index} otherwise in the decrypted file"
        )


def _page_text(access: FeatureAccess, index: int) -> PageText:
    """PDFium's characters of the page, held to max_items_per_page as glyphs() holds them."""
    cap = access.max_items_per_page
    text = access.page_text(index, cap)
    if text.stopped:
        if cap is None:  # raised, not asserted: python -O removes an assert
            raise AssertionError("the backend stops only at a cap")
        raise LimitExceededError(limit="max_items_per_page", allowed=cap, found=cap + 1)
    return text


def _annotation_views(access: FeatureAccess, index: int) -> tuple[AnnotationView | None, ...]:
    """What PDFium reads in the page's /Annots, its entries held to max_items_per_page."""
    count = access.pdfium_annotation_count(index)
    allowed = access.max_items_per_page
    if allowed is not None and count > allowed:
        raise LimitExceededError(limit="max_items_per_page", allowed=allowed, found=count)
    return access.annotation_views(index)


def _scale(geometry: PageGeometry) -> float:
    """1, or the scale s at which (width s + 1)(height s + 1) is CHECK_PIXELS:
    each side rounds up by less than a pixel, so the page is at most that
    many pixels, however long and thin it is."""
    width, height = geometry.width, geometry.height
    if math.ceil(width) * math.ceil(height) <= CHECK_PIXELS:
        return 1.0
    area, sides = width * height, width + height
    return (math.sqrt(sides * sides + 4 * area * (CHECK_PIXELS - 1)) - sides) / (2 * area)


def _decryption_error(failure: EngineFailure) -> Exception:
    # open() has had QPDF open the file with the same password.
    if failure.kind is FailureKind.FORMAT:
        return DecryptionFailedError(detail=f"QPDF cannot write the file: {failure.detail}")
    return EngineError(engine="qpdf", detail=failure.detail)
