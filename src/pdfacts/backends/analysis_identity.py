"""Private source-session identity and same-bank evidence lifetime."""

from contextlib import contextmanager
from hashlib import sha256
from math import isfinite
from threading import RLock

from pdfacts.backends.exact_stream import UnsupportedExactSource, check_source_limit
from pdfacts.backends.source_runtime import require_source_runtime

_issued = {}
_banks = {}
_path_batches = {}
_lock = RLock()
_IMMUTABLE_SCALAR_TYPES = (type(None), bool, int, str, bytes)


class _IdentityReadScope:
    __slots__ = ()
    __enter__ = staticmethod(_lock.__enter__)
    __exit__ = staticmethod(_lock.__exit__)


_identity_read_scope = _IdentityReadScope()


def _record(owner, handle, data, bank):
    if type(owner) is not object or type(handle) is not object or type(data) is not bytes:
        raise UnsupportedExactSource("analysis_identity_domain")
    record = _issued.get(owner)
    if (
        record is None
        or record[0] is not bank
        or record[1] is not handle
        or record[2] is not data
        or _banks.get(record[4]) is not owner
    ):
        raise UnsupportedExactSource("analysis_identity_binding")
    return record


def resolve_identity(owner, handle, data, bank):
    require_source_runtime()
    bank.reserve(64)  # Native RLock exit still needs its three-argument tuple.
    bank.charge(4)
    with _identity_read_scope:
        return _record(owner, handle, data, bank)[3]


def classification_identity(data, bank):
    require_source_runtime()
    return _classification_identity(data, bank)


@contextmanager
def _classification_identity(data, bank):
    require_source_runtime()
    if type(data) is not bytes:
        raise UnsupportedExactSource("analysis_identity_domain")
    bank.reserve(4096)
    bank.charge(4)
    owner = object()
    handle = object()
    bank_key = id(bank)
    try:
        with _lock:
            if bank_key in _banks:
                raise UnsupportedExactSource("analysis_identity_bank_in_use")
            _banks[bank_key] = owner
            digest = sha256(data).hexdigest()
            _issued[owner] = (bank, handle, data, digest, bank_key)
        yield owner, handle, digest
    finally:
        with _lock:
            _issued.pop(owner, None)
            _path_batches.pop(owner, None)
            if _banks.get(bank_key) is owner:
                _banks.pop(bank_key)


def recovered_identity(owner, handle, original, recovered, bank):
    require_source_runtime()
    if type(recovered) is not bytes:
        raise UnsupportedExactSource("analysis_identity_domain")
    bank.charge(4)
    with _lock:
        previous = _record(owner, handle, original, bank)
        if recovered is original:
            return handle
        bank.reserve(1024)
        bank.charge(len(recovered))
        digest = sha256(recovered).hexdigest()
        replacement = object()
        if (
            _record(owner, handle, original, bank) is not previous
            or _banks.get(previous[4]) is not owner
        ):
            raise UnsupportedExactSource("analysis_identity_binding")
        _path_batches.pop(owner, None)
        _issued[owner] = (bank, replacement, recovered, digest, previous[4])
        return replacement


def _validate_evidence_payload(value, bank, limits, depth=0):
    # Fixed-depth DFS avoids allocating a tuple iterator at every tree node.
    bank.reserve(2048)
    parents = [None] * 33
    offsets = [0] * 33
    lengths = [0] * 33
    initial_depth = depth
    while True:
        bank.charge()
        if depth > limits.max_payload_depth:
            check_source_limit("evidence_payload_depth", depth, limits.max_payload_depth)
        if type(value) in _IMMUTABLE_SCALAR_TYPES:
            pass
        elif type(value) is float:
            if not isfinite(value):
                raise UnsupportedExactSource("path_evidence_nonfinite")
        elif type(value) is tuple:
            # len() may allocate an integer. Large tuple indices also allocate.
            bank.reserve(64)
            count = len(value)
            if count > 256:
                bank.reserve(32 * count)
            if count:
                parents[depth] = value
                offsets[depth] = 1
                lengths[depth] = count
                value = value[0]
                depth += 1
                continue
        else:
            raise UnsupportedExactSource("path_evidence_payload_domain")
        while depth > initial_depth:
            bank.charge(2)
            depth -= 1
            parent = parents[depth]
            offset = offsets[depth]
            if offset < lengths[depth]:
                value = parent[offset]
                offsets[depth] = offset + 1
                depth += 1
                break
            parents[depth] = None
        else:
            return


def _store_path_evidence(owner, handle, data, bank, entries, limits):
    """Atomic private storage for a fully reconciled page's immutable payloads.

    This storage primitive does not perform or substitute for source/native
    reconciliation. The graphics issuer validates fact types before calling this private storage.
    """
    require_source_runtime()
    if type(entries) is not tuple or not entries:
        raise UnsupportedExactSource("path_evidence_batch_domain")
    check_source_limit("evidence_paths", len(entries), limits.max_paths)
    bank.reserve(1024 + 128 * len(entries))
    bank.charge(len(entries))
    with _lock:
        generation = _record(owner, handle, data, bank)
    for entry in entries:
        if (
            type(entry) is not tuple
            or len(entry) != 2
            or type(entry[0]) is not tuple
            or len(entry[0]) != 2
        ):
            raise UnsupportedExactSource("path_evidence_entry_domain")
        _validate_evidence_payload(entry[1], bank, limits)
    token = object()
    with _lock:
        if _record(owner, handle, data, bank) is not generation:
            raise UnsupportedExactSource("analysis_identity_binding")
        previous_batches = _path_batches.get(owner)
        count = 0 if previous_batches is None else len(previous_batches)
        bank.reserve(512 + 128 * count)
        bank.charge(count + 1)
        if (
            _record(owner, handle, data, bank) is not generation
            or _path_batches.get(owner) is not previous_batches
        ):
            raise UnsupportedExactSource("path_evidence_registry_changed")
        batches = {} if previous_batches is None else previous_batches.copy()
        batches[token] = (handle, entries)
        # Single publication after all validation, callbacks and allocations.
        _path_batches[owner] = batches
    return token


def _resolve_path_evidence(owner, handle, data, bank, token, ordinal, path, reference):
    require_source_runtime()
    bank.charge(8)
    if type(token) is not object or type(ordinal) is not int:
        raise UnsupportedExactSource("path_evidence_binding")
    with _lock:
        _record(owner, handle, data, bank)
        batches = _path_batches.get(owner)
        batch = None if batches is None else batches.get(token)
        if batch is None or batch[0] is not handle or not 0 <= ordinal < len(batch[1]):
            raise UnsupportedExactSource("path_evidence_binding")
        entry = batch[1][ordinal]
        if entry[0][0] is not path or entry[0][1] is not reference:
            raise UnsupportedExactSource("path_evidence_binding")
        return entry[1]
