"""Encrypted test files.

The one place where an engine helps to make a test file: the file is built
with the builder and then encrypted by pikepdf, which can also put its
objects into object streams. The encryption algorithm is not what the tests
are about, and the expected value, whether a password is needed, comes from
the passwords chosen here, not from an engine. Some values of the encryption
dictionary are pikepdf's choice, the key's length at revision 3 for one: a
test that expects such a value finds it in the bytes written.
"""

from __future__ import annotations

import io

import pikepdf


def encrypt(
    data: bytes,
    *,
    user: str | bytes,
    owner: str,
    R: int = 4,  # noqa: N803
    object_streams: bool = False,
    aes: bool = True,
    metadata: bool = True,
    allow: pikepdf.Permissions | None = None,
    compress: bool = True,
) -> bytes:
    """`data` encrypted by the standard security handler of revision `R`, 2,
    3, 4, 5 or 6; pikepdf warns that revision 5 is deprecated.

    pikepdf writes a password below revision 5 in PDFDocEncoding, from it on
    as UTF-8; there `user` may also be bytes, which it writes as they are.

    With `object_streams`, the objects that may be are put into object streams.
    `aes` and `metadata` go to pikepdf as they are, and it refuses what it
    does not write: revisions 2 and 3 only with both false, RC4 at revision 4
    only with `metadata` false, revision 6 only with `aes`. `metadata` false
    writes /EncryptMetadata false. `allow` gives the permissions, pikepdf's
    default where None. With `compress` false, a stream written without a
    filter is left without one, so its length is the length written.
    """
    mode = (
        pikepdf.ObjectStreamMode.generate if object_streams else pikepdf.ObjectStreamMode.preserve
    )
    options: dict = {"user": user, "owner": owner, "R": R, "aes": aes, "metadata": metadata}
    if allow is not None:
        options["allow"] = allow
    with pikepdf.open(io.BytesIO(data)) as pdf:
        out = io.BytesIO()
        pdf.save(
            out,
            encryption=pikepdf.Encryption(**options),
            object_stream_mode=mode,
            compress_streams=compress,
        )
    return out.getvalue()
