"""Private source-session identity and same-bank evidence lifetime."""

from contextlib import contextmanager
from hashlib import sha256
from math import isfinite
from threading import RLock

from pdfacts.backends.exact_stream import UnsupportedExactSource, check_source_limit
from pdfacts.backends.protocols import DrawnImage, DrawnPath
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
    bank.reserve(128)
    marker = object()
    holder = [None, None, marker]
    owner = object()
    handle = object()
    bank_key = id(bank)
    try:
        with _lock:
            if bank_key in _banks:
                raise UnsupportedExactSource("analysis_identity_bank_in_use")
            _banks[bank_key] = owner
            digest = sha256(data).hexdigest()
            _issued[owner] = (bank, handle, data, digest, bank_key, holder)
        yield owner, handle, digest
    finally:
        with _lock:
            current = _issued.get(owner)
            if current is not None:
                current[5][0] = None
                current[5][1] = None
            _issued.pop(owner, None)
            _path_batches.pop(owner, None)
            if _banks.get(bank_key) is owner:
                _banks.pop(bank_key)


def recovered_identity(owner, handle, original, recovered, bank):
    require_source_runtime()
    if type(recovered) is not bytes:
        raise UnsupportedExactSource("analysis_identity_domain")
    bank.reserve(128)
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
        marker = object()
        holder = [None, None, marker]
        previous[5][0] = None
        previous[5][1] = None
        _issued[owner] = (bank, replacement, recovered, digest, previous[4], holder)
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


def _resolve_path_evidence(
    owner, handle, data, bank, token, ordinal, path, reference, *, support=False
):
    require_source_runtime()
    bank.charge(8)
    if type(token) is not object or type(ordinal) is not int:
        raise UnsupportedExactSource("path_evidence_binding")
    with _lock:
        _record(owner, handle, data, bank)
        batches = _path_batches.get(owner)
        batch = None if batches is None else batches.get(token)
        if (
            type(batch) is not tuple
            or len(batch) != (3 if support else 2)
            or (support and (type(batch[2]) is not str or batch[2] != "root-support-v1"))
            or batch[0] is not handle
            or not 0 <= ordinal < len(batch[1])
        ):
            raise UnsupportedExactSource("path_evidence_binding")
        entry = batch[1][ordinal]
        if entry[0][0] is not path or entry[0][1] is not reference:
            raise UnsupportedExactSource("path_evidence_binding")
        return entry[1]


def _root_support_inventory_size(owner, handle, data, bank, token):
    """Read the authenticated page and root count, never caller collections."""
    require_source_runtime()
    bank.reserve(512)  # Reader frame, count integer, result pair and lock exit.
    bank.charge(24)
    if type(token) is not object:
        raise UnsupportedExactSource("path_evidence_binding")
    with _identity_read_scope:
        _record(owner, handle, data, bank)
        batches = _path_batches.get(owner)
        batch = None if batches is None else batches.get(token)
        if (
            type(batch) is not tuple
            or len(batch) != 3
            or batch[0] is not handle
            or type(batch[2]) is not str
            or batch[2] != "root-support-v1"
            or type(batch[1]) is not tuple
            or not batch[1]
        ):
            raise UnsupportedExactSource("path_evidence_binding")
        # Stored references and entries are frozen by the complete-page issuer.
        # No callback follows the identity check before the pair is published.
        return batch[1][0][1][0][0], len(batch[1])


def _staged_document(document, document_types, provider_types):
    """Exact module-owned classes precede every exposed object's attribute read."""
    if type(document) is not document_types[0]:
        raise UnsupportedExactSource("staged_source_binding")
    pdfium = document._pdfium
    provider = document._qpdf
    if (
        type(pdfium) is not provider_types[0]
        or type(provider) is not provider_types[1]
        or document._closed is not False
        or pdfium._closed is not False
        or provider._closed is not False
        or type(document._read) is not dict
    ):
        raise UnsupportedExactSource("staged_source_binding")
    return provider


def _staged_binding(document, generation, binding, bank, limits, document_types, provider_types):
    """Callback-free Python identity checks; caller owns engine then identity lock."""
    provider = _staged_document(document, document_types, provider_types)
    owner, handle = binding[3]
    if (
        _record(owner, handle, generation[2], bank) is not generation
        or document is not binding[0]
        or document._pdfium is not binding[1]
        or provider is not binding[2]
        or binding[1]._data is not generation[2]
        or provider._data is not generation[2]
        or getattr(provider, "_analysis_identity", None) is not binding[3]
        or limits is not binding[7]
    ):
        raise UnsupportedExactSource("staged_source_binding")
    page = document._read.get(binding[5])
    if type(page) is not document_types[1] or page is not binding[6][0]:
        raise UnsupportedExactSource("path_evidence_page_binding")
    ref = page._ref
    if (
        type(ref) is not document_types[2]
        or ref is not binding[6][1]
        or type(ref.page_index) is not int
        or type(ref.object_number) is not int
        or type(ref.generation) is not int
        or ref.page_index != binding[5]
        or ref.object_number != binding[6][2]
        or ref.generation != binding[6][3]
    ):
        raise UnsupportedExactSource("path_evidence_page_binding")


def _stage_generation(document, bank, document_types, provider_types):
    bank.reserve(64)
    bank.charge(32)
    with _identity_read_scope:
        provider = _staged_document(document, document_types, provider_types)
        binding = getattr(provider, "_analysis_identity", None)
        if type(binding) is not tuple or len(binding) != 2:
            raise UnsupportedExactSource("staged_source_binding")
        generation = _record(binding[0], binding[1], provider._data, bank)
        if generation[5][1] is not None:
            raise UnsupportedExactSource("staged_source_active")
        return generation


def _publish_staged_capture(
    document, generation, binding, captured, bank, limits, document_types, provider_types
):
    # Capture's 1280 control envelope pays this node and these two exits.
    with _identity_read_scope:
        _staged_binding(document, generation, binding, bank, limits, document_types, provider_types)
        holder = generation[5]
        revision = holder[2]
        if holder[1] is not None:
            raise UnsupportedExactSource("staged_source_active")
    token = object()
    node = [token, binding, captured, None, None]
    # No callbacks occur between snapshot and publication, but the marker still
    # protects against another identity operation while the lock was released.
    with _identity_read_scope:
        _staged_binding(document, generation, binding, bank, limits, document_types, provider_types)
        if holder[2] is not revision or holder[1] is not None:
            raise UnsupportedExactSource("staged_source_changed")
        node[3] = holder[0]
        holder[0] = node
        holder[2] = token
    return token


def _abort_staged(generation, token):
    """The completion setup prepaid this exit; never consult the exhausted bank."""
    with _identity_read_scope:
        active = generation[5][1]
        if active is not None and active[0] is token:
            generation[5][1] = None


def _abort_staged_owner(owner, token):
    """Graphics cleanup resolves storage internally without exporting its generation.

    A recovery clears the old holder. A fresh generation cannot own this exact
    one-shot token, so this never clears a later generation's active operation.
    The core setup already prepaid this exit.
    """
    with _identity_read_scope:
        generation = _issued.get(owner)
        if generation is not None:
            active = generation[5][1]
            if active is not None and active[0] is token:
                generation[5][1] = None


def _claim_staged(document, token, bank, limits, document_types, provider_types):
    bank.reserve(512)
    bank.charge(128)
    bank.reserve(64)
    with _identity_read_scope:
        provider = _staged_document(document, document_types, provider_types)
        binding = getattr(provider, "_analysis_identity", None)
        if type(token) is not object or type(binding) is not tuple or len(binding) != 2:
            raise UnsupportedExactSource("staged_source_token")
        generation = _record(binding[0], binding[1], provider._data, bank)
        holder = generation[5]
        if holder[1] is not None:
            raise UnsupportedExactSource("staged_source_active")
        revision = holder[2]
        node = holder[0]
    previous = None
    marker = object()
    try:
        while node is not None:
            bank.reserve(64)
            bank.charge(8)
            with _identity_read_scope:
                if (
                    _record(binding[0], binding[1], generation[2], bank) is not generation
                    or generation[5] is not holder
                    or holder[2] is not revision
                    or holder[1] is not None
                ):
                    raise UnsupportedExactSource("staged_source_changed")
                if node[0] is token:
                    _staged_binding(
                        document, generation, node[1], bank, limits, document_types, provider_types
                    )
                    if previous is None:
                        holder[0] = node[3]
                    else:
                        previous[3] = node[3]
                    node[3] = None
                    holder[1] = node
                    holder[2] = marker
                    return node
                previous = node
                node = node[3]
        raise UnsupportedExactSource("staged_source_token")
    except BaseException:
        _abort_staged(generation, token)
        raise


def _ready_staged(
    document, token, generation, result, bank, limits, document_types, provider_types
):
    bank.reserve(64)
    bank.charge(64)
    with _identity_read_scope:
        node = generation[5][1]
        if node is None or node[0] is not token:
            raise UnsupportedExactSource("staged_source_token")
        _staged_binding(document, generation, node[1], bank, limits, document_types, provider_types)
        node[4] = result


def _check_staged_refs(node, entries, graphic_types, support=False):
    """Final whole-root guard; work/counters prepaid outside the identity lock."""
    roots = node[2][2]
    references = node[1][8]
    index = 0
    path_index = 0
    count = len(roots)
    entry_count = len(entries)
    while index < count:
        root = roots[index]
        ref = references[index]
        native_type = type(root[0])
        if native_type is DrawnPath:
            graphic_type = graphic_types[0]
        elif native_type is DrawnImage:
            graphic_type = graphic_types[1]
        else:
            raise UnsupportedExactSource("path_evidence_record_domain")
        graphic = root[1]
        if type(graphic) is not graphic_type or type(ref) is not graphic_types[2]:
            raise UnsupportedExactSource("path_evidence_record_domain")
        if (
            graphic.ref is not ref
            or type(ref.page_index) is not int
            or type(ref.drawing_index) is not int
            or ref.page_index != node[1][5]
            or ref.drawing_index != index
            or ref.annotation is not None
        ):
            raise UnsupportedExactSource("path_evidence_ref_changed")
        if support or type(root[0]) is DrawnPath:
            if (
                path_index >= entry_count
                or entries[path_index][0][0] is not root[1]
                or entries[path_index][0][1] is not ref
            ):
                raise UnsupportedExactSource("path_evidence_path_binding")
            path_index += 1
        index += 1
    if path_index != entry_count:
        raise UnsupportedExactSource("path_evidence_inventory_count")


def _store_staged_path_evidence(
    document,
    pending,
    bank,
    entries,
    limits,
    document_types,
    provider_types,
    graphic_types,
    *,
    support=False,
):
    """Prepare outside the identity lock; publish only the internally ready token."""
    require_source_runtime()
    if type(entries) is not tuple:
        raise UnsupportedExactSource("path_evidence_batch_domain")
    if support:
        check_source_limit("evidence_roots", len(entries), limits.max_root_objects)
        bank.reserve(64)
        bank.charge(64)
    else:
        check_source_limit("evidence_paths", len(entries), limits.max_paths)
    bank.reserve(1024 + 128 * len(entries))
    bank.charge(len(entries))
    bank.reserve(64)
    bank.charge(64)
    with _identity_read_scope:
        provider = _staged_document(document, document_types, provider_types)
        binding = getattr(provider, "_analysis_identity", None)
        if type(binding) is not tuple or len(binding) != 2:
            raise UnsupportedExactSource("staged_source_binding")
        owner, handle = binding
        generation = _record(owner, handle, provider._data, bank)
        node = generation[5][1]
        if node is None or node[0] is not pending or node[4] is None:
            raise UnsupportedExactSource("staged_source_token")
        _staged_binding(document, generation, node[1], bank, limits, document_types, provider_types)
        ready = node[4]
        previous_batches = _path_batches.get(owner)
    for entry in entries:
        if (
            type(entry) is not tuple
            or len(entry) != 2
            or type(entry[0]) is not tuple
            or len(entry[0]) != 2
        ):
            raise UnsupportedExactSource("path_evidence_entry_domain")
        _validate_evidence_payload(entry[1], bank, limits)
    count = 0 if previous_batches is None else len(previous_batches)
    bank.reserve(512 + 128 * count)
    bank.charge(count + 1)
    token = object()  # Also the explicitly prepaid image-only revision marker.
    batches = None
    if entries:
        batches = {} if previous_batches is None else previous_batches.copy()
        batches[token] = (handle, entries, "root-support-v1") if support else (handle, entries)
    bank.reserve(512)
    root_count = len(node[2][2])
    bank.reserve(192 + (72 * (root_count - 256) if root_count > 256 else 0))
    bank.charge((28 if support else 24) * root_count + 96)
    bank.reserve(64)
    with _identity_read_scope:
        if (
            generation[5][1] is not node
            or node[0] is not pending
            or node[4] is not ready
            or _path_batches.get(owner) is not previous_batches
        ):
            raise UnsupportedExactSource("staged_source_changed")
        _staged_binding(document, generation, node[1], bank, limits, document_types, provider_types)
        _check_staged_refs(node, entries, graphic_types, support)
        if batches is not None:
            _path_batches[owner] = batches
        generation[5][1] = None
        generation[5][2] = token
    return token if entries else None
