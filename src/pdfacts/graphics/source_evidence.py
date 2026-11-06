"""Private same-source association issuer. Payloads grant no write authority."""

from fractions import Fraction
from math import copysign, isfinite

from pdfacts.backends.analysis_identity import (
    _abort_staged_owner,
    _publish_staged_capture,
    _resolve_path_evidence,
    _stage_generation,
    _store_path_evidence,
    _store_staged_path_evidence,
    resolve_identity,
)
from pdfacts.backends.exact_numbers import SourceImageNumbers, SourcePathNumbers
from pdfacts.backends.exact_stream import (
    ExactSourceLimit,
    UnsupportedExactSource,
    check_source_limit,
)
from pdfacts.backends.protocols import DrawnImage, DrawnPath, OperationBudget, SourceEvidenceLimits
from pdfacts.core.document import _STAGED_DOCUMENT_TYPES, _STAGED_PROVIDER_TYPES
from pdfacts.core.errors import LimitExceededError, SourceEvidenceError
from pdfacts.core.geometry import Point, Quad
from pdfacts.core.refs import GraphicRef
from pdfacts.core.source_runtime import require_source_runtime
from pdfacts.core.values import Unknown
from pdfacts.graphics.images import Image
from pdfacts.graphics.paths import Path
from pdfacts.graphics.reader import _image as _public_image
from pdfacts.graphics.reader import paths_with_root_inventory

_STAGED_GRAPHIC_TYPES = (Path, Image, GraphicRef)


_SOURCE_FIELDS = (
    "stream_identity",
    "instruction",
    "ordinal",
    "matrix",
    "width",
    "operations",
    "paint",
    "line_cap",
    "line_join",
    "miter_limit",
)
_NATIVE_FIELDS = (
    "matrix",
    "segments",
    "fill_mode",
    "stroke",
    "fill_colour",
    "stroke_colour",
    "width",
    "dash",
    "dash_phase",
    "cap",
    "join",
    "source_level",
    "source_clip_count",
)
_IMAGE_SOURCE_FIELDS = (
    "instruction",
    "resource_identity",
    "matrix",
    "clip",
    "pixel_width",
    "pixel_height",
    "colour_space",
    "bits_per_pixel",
)
_SCALAR_TYPES = (type(None), bool, int, str, bytes)
_REQUIRED_PATH_FIELDS = (
    "subpaths",
    "fill",
    "stroke",
    "fill_colour",
    "stroke_colour",
    "line_width",
    "dash",
    "line_cap",
    "line_join",
)


def _freeze(value, bank, limits, depth=0, matrix_hint=None):
    """Prepay additional representation allocations before constructing them."""
    bank.charge(2)
    if depth > limits.max_payload_depth:
        check_source_limit("evidence_payload_depth", depth, limits.max_payload_depth)
    if matrix_hint is not None:
        bank.charge(2)
        if value is matrix_hint[0]:
            # The qualified six-Fraction input has height one. Fractions are
            # atomic inputs even though their frozen representations are tuples.
            if depth >= limits.max_payload_depth:
                check_source_limit("evidence_payload_depth", depth + 1, limits.max_payload_depth)
            return matrix_hint[1]
    if type(value) in _SCALAR_TYPES:
        return value
    if type(value) is Fraction:
        bank.reserve(128)
        if type(value) is not Fraction:
            raise UnsupportedExactSource("path_evidence_payload_domain")
        return ("fraction", value.numerator, value.denominator)
    if type(value) is float:
        if not isfinite(value):
            raise UnsupportedExactSource("path_evidence_nonfinite")
        return value
    if type(value) is not tuple:
        raise UnsupportedExactSource("path_evidence_payload_domain")
    bank.reserve(192)
    count = len(value)
    if count > 256:
        bank.reserve(36 * (count - 256))
    bank.charge(count)
    items = None
    index = 0
    while index < count:
        member = value[index]
        frozen = _freeze(member, bank, limits, depth + 1, matrix_hint)
        if frozen is not member and items is None:
            bank.reserve(128 + 16 * count)
            items = list(value)
        if items is not None:
            items[index] = frozen
        index += 1
    return value if items is None else tuple(items)


def _same_native(left, right, bank):
    bank.charge()
    if type(left) is not type(right):
        return False
    if type(left) is float:
        if not isfinite(left) or not isfinite(right):
            return False
        if left == 0.0 and right == 0.0:
            bank.reserve(64)
            return copysign(1.0, left) == copysign(1.0, right)
        return left == right
    if type(left) is not tuple:
        return left == right
    bank.reserve(192)
    count = len(left)
    if count != len(right):
        return False
    if count > 256:
        bank.reserve(36 * (count - 256))
    index = 0
    while index < count:
        if not _same_native(left[index], right[index], bank):
            return False
        index += 1
    return True


def _record_payload(record, names, bank, limits, source_cache=None):
    # Closed module-owned type/field pairs; no witness supplied by the record.
    if names is _SOURCE_FIELDS:
        expected_type = SourcePathNumbers
    elif names is _NATIVE_FIELDS:
        expected_type = DrawnPath
    elif names is _IMAGE_SOURCE_FIELDS:
        expected_type = SourceImageNumbers
    elif names is _IMAGE_NATIVE_FIELDS:
        expected_type = DrawnImage
    else:
        raise UnsupportedExactSource("path_evidence_record_domain")
    bank.reserve(192)
    count = len(names)
    bank.reserve(192 + 16 * count)
    if count > 256:
        bank.reserve(36 * (count - 256))
    bank.charge(16 + 5 * count)
    values = [None] * count
    matrix_hint = None
    index = 0
    while index < count:
        name = names[index]
        if type(record) is not expected_type:
            raise UnsupportedExactSource("path_evidence_record_domain")
        value = getattr(record, name)
        entry = None if source_cache is None else source_cache.get(name)
        bank.charge()
        if entry is not None and entry[0] is value:
            values[index] = entry[1]
        else:
            values[index] = _freeze(
                value,
                bank,
                limits,
                0,
                matrix_hint if name == "operations" else None,
            )
            if source_cache is not None and name in source_cache:
                bank.reserve(128)
                source_cache[name] = (value, values[index])
        if source_cache is not None and name == "matrix":
            bank.charge(8)
            matrix_hint = None
            if (
                type(value) is tuple
                and len(value) == 6
                and type(value[0]) is Fraction
                and type(value[1]) is Fraction
                and type(value[2]) is Fraction
                and type(value[3]) is Fraction
                and type(value[4]) is Fraction
                and type(value[5]) is Fraction
            ):
                matrix_hint = source_cache.get("matrix")
        index += 1
    if type(record) is not expected_type:
        raise UnsupportedExactSource("path_evidence_record_domain")
    return tuple(values)


_IMAGE_NATIVE_FIELDS = ("matrix", "width", "height", "colour_space", "bits_per_pixel")
_IMAGE_PIXEL_FIELDS = ("pixel_width", "pixel_height", "bits_per_pixel")
_IMAGE_CORNER_FIELDS = ("p1", "p2", "p3", "p4")


def _verify_public_image(native, reconciled, image, bank):
    """Caller prepays the public conversion and bounded comparison inventory."""
    if (
        type(native) is not DrawnImage
        or type(reconciled) is not DrawnImage
        or type(image) is not Image
    ):
        raise UnsupportedExactSource("path_evidence_record_domain")
    bank.charge(64)
    bank.charge(32)
    for name in _IMAGE_NATIVE_FIELDS:
        if type(native) is not DrawnImage or type(reconciled) is not DrawnImage:
            raise UnsupportedExactSource("path_evidence_record_domain")
        if not _same_native(getattr(native, name), getattr(reconciled, name), bank):
            raise UnsupportedExactSource("path_evidence_inventory_binding")
    if (
        type(native) is not DrawnImage
        or type(reconciled) is not DrawnImage
        or type(image) is not Image
        or type(image.ref) is not GraphicRef
        or type(image.corners) is not Quad
    ):
        raise UnsupportedExactSource("path_evidence_image_unknown")
    # The existing conversion computes the full, unclipped unit-square corners.
    # It does not read or decode image data.
    expected = _public_image(native, native.matrix, image.ref, None)
    if type(expected.corners) is not Quad or isinstance(expected.colour_space, Unknown):
        raise UnsupportedExactSource("path_evidence_image_unknown")
    if image.colour_space is not expected.colour_space:
        raise UnsupportedExactSource("path_evidence_image_disagreement")
    for name in _IMAGE_PIXEL_FIELDS:
        if type(image) is not Image:
            raise UnsupportedExactSource("path_evidence_record_domain")
        actual_value = getattr(image, name)
        expected_value = getattr(expected, name)
        if type(actual_value) is not int or not _same_native(actual_value, expected_value, bank):
            raise UnsupportedExactSource("path_evidence_image_disagreement")
    for name in _IMAGE_CORNER_FIELDS:
        if type(image) is not Image or type(image.corners) is not Quad:
            raise UnsupportedExactSource("path_evidence_record_domain")
        actual_point = getattr(image.corners, name)
        expected_point = getattr(expected.corners, name)
        if (
            type(actual_point) is not Point
            or not _same_native(actual_point.x, expected_point.x, bank)
            or type(actual_point) is not Point
            or not _same_native(actual_point.y, expected_point.y, bank)
        ):
            raise UnsupportedExactSource("path_evidence_image_disagreement")

    if (
        type(image) is not Image
        or type(image.ref) is not GraphicRef
        or type(image.ref.page_index) is not int
        or type(image.ref.drawing_index) is not int
    ):
        raise UnsupportedExactSource("path_evidence_record_domain")


def _register_path_evidence(
    owner, handle, data, bank, entries, limits, *, document=None, pending=None, support=False
):
    """Validate feature types and freeze references before atomic opaque storage."""
    require_source_runtime()
    if type(entries) is not tuple:
        raise UnsupportedExactSource("path_evidence_batch_domain")
    bank.reserve(128)
    bank.charge(2)
    bank.reserve(512 + 288 * len(entries))
    bank.charge((20 if support else 16) * len(entries))
    checked = [None] * len(entries)
    index = 0
    for entry in entries:
        if (
            type(entry) is not tuple
            or len(entry) != 2
            or (type(entry[0]) is not Path and not (support and type(entry[0]) is Image))
        ):
            raise UnsupportedExactSource("path_evidence_entry_domain")
        path, payload = entry
        ref = path.ref
        if (
            type(ref) is not GraphicRef
            or ref.annotation is not None
            or type(ref.page_index) is not int
            or type(ref.drawing_index) is not int
            or ref.page_index < 0
            or ref.drawing_index < 0
        ):
            raise UnsupportedExactSource("path_evidence_ref_domain")
        reference = (ref.page_index, ref.drawing_index)
        checked[index] = ((path, ref), (reference, payload))
        index += 1
    if pending is not None:
        return _store_staged_path_evidence(
            document,
            pending,
            bank,
            tuple(checked),
            limits,
            _STAGED_DOCUMENT_TYPES,
            _STAGED_PROVIDER_TYPES,
            _STAGED_GRAPHIC_TYPES,
            support=support,
        )
    return _store_path_evidence(owner, handle, data, bank, tuple(checked), limits)


def resolve_path_evidence(owner, handle, data, bank, token, ordinal, path):
    """Resolve the original Path object and reject changed reference identity."""
    return _resolve_graphic_evidence(owner, handle, data, bank, token, ordinal, path, False)


def resolve_root_support_evidence(owner, handle, data, bank, token, root_ordinal, graphic):
    """Resolve original mixed-root support association; no positive write authority."""
    return _resolve_graphic_evidence(owner, handle, data, bank, token, root_ordinal, graphic, True)


def _resolve_graphic_evidence(owner, handle, data, bank, token, ordinal, path, support):
    require_source_runtime()
    bank.reserve(256)
    bank.charge(24)
    try:
        if type(path) is not Path and not (support and type(path) is Image):
            raise UnsupportedExactSource("path_evidence_entry_domain")
        captured_ref = path.ref
        if type(captured_ref) is not GraphicRef:
            raise UnsupportedExactSource("path_evidence_entry_domain")
        reference, payload = _resolve_path_evidence(
            owner,
            handle,
            data,
            bank,
            token,
            ordinal,
            path,
            captured_ref,
            support=support,
        )
        if (
            (type(path) is not Path and not (support and type(path) is Image))
            or type(captured_ref) is not GraphicRef
            or captured_ref.annotation is not None
            or type(captured_ref.page_index) is not int
            or type(captured_ref.drawing_index) is not int
            or captured_ref.page_index != reference[0]
            or captured_ref.drawing_index != reference[1]
        ):
            raise UnsupportedExactSource("path_evidence_ref_changed")
        # Lower storage can invoke the caller's budget. Check identity after
        # its final callback, with no subsequent callback before publication.
        if (
            (type(path) is not Path and not (support and type(path) is Image))
            or type(captured_ref) is not GraphicRef
            or path.ref is not captured_ref
        ):
            raise UnsupportedExactSource("path_evidence_ref_changed")
        return payload
    except UnsupportedExactSource as failure:
        raise SourceEvidenceError(reason=str(failure)) from failure


def classification_identity(data, bank):
    """Create a source-session context only on the proved runtime."""
    from pdfacts.backends.analysis_identity import classification_identity as backend_session

    require_source_runtime()
    return backend_session(data, bank)


def issue_root_path_evidence(document, page_index, bank: OperationBudget, *, limits):
    """Private source association with caller-owned limits and typed failures."""
    require_source_runtime()
    if type(limits) is not SourceEvidenceLimits:
        raise TypeError("limits must be SourceEvidenceLimits")
    bank.reserve(256)
    bank.charge(2)
    try:
        return _issue_root_path_evidence(document, page_index, bank, limits=limits)
    except ExactSourceLimit as failure:
        raise LimitExceededError(
            limit=failure.limit,
            allowed=failure.allowed,
            found=failure.found,
        ) from failure
    except UnsupportedExactSource as failure:
        raise SourceEvidenceError(reason=str(failure)) from failure


def capture_root_path_evidence(document, page_index, bank: OperationBudget, *, limits):
    """Retain original ordinary facts; pending tokens carry no source certificate."""
    require_source_runtime()
    if type(limits) is not SourceEvidenceLimits:
        raise TypeError("limits must be SourceEvidenceLimits")
    bank.reserve(256)
    bank.charge(2)
    try:
        with document._access() as access:
            return _capture_root_path_evidence(document, page_index, bank, access, limits)
    except ExactSourceLimit as failure:
        raise LimitExceededError(
            limit=failure.limit, allowed=failure.allowed, found=failure.found
        ) from failure
    except UnsupportedExactSource as failure:
        raise SourceEvidenceError(reason=str(failure)) from failure


def complete_root_path_evidence(document, bank: OperationBudget, pending_token, *, limits):
    """Complete the entire original page and consume its pending token once."""
    return _complete_root_evidence(document, bank, pending_token, limits, False)


def complete_root_support_evidence(document, bank: OperationBudget, pending_token, *, limits):
    """Complete one mixed-root support batch using the original single pending claim."""
    return _complete_root_evidence(document, bank, pending_token, limits, True)


def _complete_root_evidence(document, bank, pending_token, limits, support):
    require_source_runtime()
    if type(limits) is not SourceEvidenceLimits:
        raise TypeError("limits must be SourceEvidenceLimits")
    # Explicit staging adds an access context; eager issuance shares its outer one.
    bank.reserve(4096)
    bank.charge(32)
    try:
        with document._access() as access:
            return _complete_root_path_evidence(
                document, bank, pending_token, access, limits, support
            )
    except ExactSourceLimit as failure:
        raise LimitExceededError(
            limit=failure.limit, allowed=failure.allowed, found=failure.found
        ) from failure
    except UnsupportedExactSource as failure:
        raise SourceEvidenceError(reason=str(failure)) from failure


def _capture_root_path_evidence(document, page_index, bank, access, limits):
    if bank is None or type(page_index) is not int:
        raise UnsupportedExactSource("path_evidence_issuer_domain")
    bank.reserve(1280)
    bank.charge(192)
    provider = access._qpdf_ready()
    binding = getattr(provider, "_analysis_identity", None)
    if binding is None:
        raise UnsupportedExactSource("analysis_identity_binding")
    generation = _stage_generation(document, bank, _STAGED_DOCUMENT_TYPES, _STAGED_PROVIDER_TYPES)
    bank.reserve(32)
    bank.charge()
    check_source_limit("source_bytes", len(generation[2]), limits.max_source_bytes)
    captured = paths_with_root_inventory(
        document, page_index, bank, limits=limits, _with_native_capture=True
    )
    if isinstance(captured, Unknown):
        raise UnsupportedExactSource("path_evidence_capture_unknown")
    paths, images, roots, capture_token = captured
    native = access.consume_staged_root_capture(page_index, bank, capture_token, limits)
    page = document.pages[page_index]
    page_ref = page.ref
    bank.reserve(512)
    count = len(roots)
    bank.reserve(192 + 16 * count)
    if count > 256:
        bank.reserve(36 * (count - 256))
    bank.charge(8 * count)
    refs = [None] * count
    index = 0
    while index < count:
        refs[index] = roots[index][1].ref
        index += 1
    refs = tuple(refs)
    bound = (
        document,
        access._pdfium_ready(),
        provider,
        binding,
        generation,
        page_index,
        (page, page_ref, page_ref.object_number, page_ref.generation),
        limits,
        refs,
    )
    retained = (paths, images, roots, native)
    token = _publish_staged_capture(
        document,
        generation,
        bound,
        retained,
        bank,
        limits,
        _STAGED_DOCUMENT_TYPES,
        _STAGED_PROVIDER_TYPES,
    )
    return paths, images, token


def _issue_root_path_evidence(document, page_index, bank: OperationBudget, *, limits):
    """Preserve eager issuance with a shared access scope and new ordinary objects."""
    require_source_runtime()
    if bank is None or type(page_index) is not int:
        raise UnsupportedExactSource("path_evidence_issuer_domain")
    with document._access() as access:
        paths, images, pending = _capture_root_path_evidence(
            document, page_index, bank, access, limits
        )
        token = _complete_root_path_evidence(document, bank, pending, access, limits)
        return paths, images, token


def _complete_root_path_evidence(document, bank, pending, access, limits, support=False):
    binding = getattr(access._qpdf_ready(), "_analysis_identity", None)
    owner = None if binding is None else binding[0]
    completed = access.complete_staged_root_numbers(document, pending, bank, limits=limits)
    # Core cleans up its own failures. No callback lies between its successful
    # return and arming graphics cleanup; it prepaid the matching abort exit.
    paths, images, roots, result, page_index = completed
    try:
        return _publish_root_path_evidence(
            document, bank, pending, limits, paths, images, roots, result, page_index, support
        )
    finally:
        _abort_staged_owner(owner, pending)


def _publish_root_path_evidence(
    document, bank, pending, limits, paths, images, roots, result, page_index, support=False
):
    source_hash, page_ref, streams, joined = result
    provider = document._qpdf
    owner, handle = provider._analysis_identity
    digest = resolve_identity(owner, handle, provider._data, bank)
    page = document.pages[page_index].ref
    bank.reserve(512)
    bank.charge(8)
    if source_hash != digest or page_ref != (page.object_number, page.generation):
        raise UnsupportedExactSource("path_evidence_page_binding")
    if len(roots) != len(joined) or (not support and not roots):
        raise UnsupportedExactSource("path_evidence_inventory_count")
    check_source_limit("native_root_objects", len(roots), limits.max_root_objects)
    bank.reserve(1024 + 128 * len(roots))
    bank.charge(len(roots))
    path_count = 0
    for native, _graphic in roots:
        bank.charge()
        if type(native) is DrawnPath:
            path_count += 1
    check_source_limit("evidence_paths", path_count, limits.max_paths)
    entries = [None] * (len(roots) if support else path_count)
    path_ordinal = 0
    image_ordinal = 0
    # Fixed four-entry identity cache, owned only by this issuance.
    bank.reserve(1024)
    source_cache = dict(matrix=None, width=None, miter_limit=None, stream_identity=None)
    for ordinal, (root, exact) in enumerate(zip(roots, joined, strict=True)):
        native, path = root
        exact_ordinal, source, reconciled_native = exact
        if type(native) is DrawnImage:
            bank.reserve(8192)
            _verify_public_image(native, reconciled_native, path, bank)
            if (
                type(source) is not SourceImageNumbers
                or exact_ordinal != ordinal
                or path.ref.page_index != page_index
                or path.ref.drawing_index != ordinal
                or path.ref.annotation is not None
                or path.annotation_flags is not None
                or image_ordinal >= len(images)
                or images[image_ordinal] is not path
            ):
                raise UnsupportedExactSource("path_evidence_image_binding")
            if support:
                bank.reserve(512)
                payload = (
                    "root-image-association-v1",
                    digest,
                    page_index,
                    page_ref,
                    streams,
                    _record_payload(source, _IMAGE_SOURCE_FIELDS, bank, limits),
                    _record_payload(native, _IMAGE_NATIVE_FIELDS, bank, limits),
                )
                entries[ordinal] = (path, payload)
            image_ordinal += 1
            continue
        if (
            type(native) is not DrawnPath
            or type(reconciled_native) is not DrawnPath
            or type(path) is not Path
            or type(source) is not SourcePathNumbers
        ):
            raise UnsupportedExactSource("path_evidence_record_domain")
        if type(native.segments) is not tuple:
            raise UnsupportedExactSource("path_evidence_native_unknown")
        bank.charge(64 + 8 * len(native.segments))
        if (
            exact_ordinal != ordinal
            or source.ordinal != path_ordinal
            or path.ref.page_index != page_index
            or path.ref.drawing_index != ordinal
            or path.ref.annotation is not None
            or path.annotation_flags is not None
            or native.source_level != 0
            or path.source_level != 0
        ):
            raise UnsupportedExactSource("path_evidence_inventory_binding")
        bank.reserve(256)
        for name in _NATIVE_FIELDS:
            bank.charge()
            if not _same_native(getattr(native, name), getattr(reconciled_native, name), bank):
                raise UnsupportedExactSource("path_evidence_inventory_binding")
        for name in _REQUIRED_PATH_FIELDS:
            bank.charge()
            if isinstance(getattr(path, name), Unknown):
                raise UnsupportedExactSource("path_evidence_path_unknown")
        # The public Path must be one of this read's exact returned objects.
        if path_ordinal >= len(paths) or paths[path_ordinal] is not path:
            raise UnsupportedExactSource("path_evidence_path_binding")
        bank.reserve(512)
        payload = (
            "root-association-v2",
            digest,
            page_index,
            page_ref,
            streams,
            _record_payload(source, _SOURCE_FIELDS, bank, limits, source_cache),
            _record_payload(native, _NATIVE_FIELDS, bank, limits),
        )
        entries[ordinal if support else path_ordinal] = (path, payload)
        path_ordinal += 1
    if path_ordinal != path_count:
        raise UnsupportedExactSource("path_evidence_inventory_count")
    # Image-only completion still performs the same atomic lifecycle/ref guard.
    return _register_path_evidence(
        None,
        None,
        None,
        bank,
        tuple(entries),
        limits,
        document=document,
        pending=pending,
        support=support,
    )
