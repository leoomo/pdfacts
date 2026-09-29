"""Encrypted test files.

The one place where an engine helps to make a test file: the file is built
with the builder and then encrypted by pikepdf, which can also put its
objects into object streams. The encryption algorithm is not what the tests
are about, and the expected value, whether a password is needed, comes from
the passwords chosen here, not from an engine.
"""

from __future__ import annotations

import io

import pikepdf


def encrypt(
    data: bytes,
    *,
    user: str,
    owner: str,
    R: int = 4,  # noqa: N803
    object_streams: bool = False,
) -> bytes:
    """`data` encrypted by the standard security handler of revision `R`, 4 or 6.

    With `object_streams`, the objects that may be are put into object streams.
    """
    mode = (
        pikepdf.ObjectStreamMode.generate if object_streams else pikepdf.ObjectStreamMode.preserve
    )
    with pikepdf.open(io.BytesIO(data)) as pdf:
        out = io.BytesIO()
        pdf.save(
            out,
            encryption=pikepdf.Encryption(user=user, owner=owner, R=R),
            object_stream_mode=mode,
        )
    return out.getvalue()
