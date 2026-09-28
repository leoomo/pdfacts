"""Encrypted test files.

The one place where an engine helps to make a test file: the file is built
with the builder and then encrypted by pikepdf. The encryption algorithm is
not what the tests are about, and the expected value, whether a password is
needed, comes from the passwords chosen here, not from an engine.
"""

from __future__ import annotations

import io

import pikepdf


def encrypt(data: bytes, *, user: str, owner: str, R: int = 4) -> bytes:  # noqa: N803
    """`data` encrypted by the standard security handler of revision `R`, 4 or 6."""
    with pikepdf.open(io.BytesIO(data)) as pdf:
        out = io.BytesIO()
        pdf.save(out, encryption=pikepdf.Encryption(user=user, owner=owner, R=R))
    return out.getvalue()
