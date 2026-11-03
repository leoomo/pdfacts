"""Runtime domain of the private cumulative-allocation proofs."""

import struct
import sys
import sysconfig

from pdfacts.backends.exact_stream import UnsupportedExactSource

# This is a source-proof domain, not a size-sampling heuristic. Ordinary
# library operations do not consult this predicate.
_SUPPORTED_RUNTIME = (
    sys.implementation.name == "cpython"
    and sys.version_info.major == 3
    and sys.version_info.minor == 13
    and sys.version_info.micro == 5
    and sys.version_info.releaselevel == "final"
    and sys.version_info.serial == 0
    and struct.calcsize("P") == 8
    and sys.int_info.bits_per_digit == 30
    and sys.int_info.sizeof_digit == 4
    and not sysconfig.get_config_var("Py_GIL_DISABLED")
    and not sysconfig.get_config_var("Py_DEBUG")
    and not hasattr(sys, "getobjects")
)


def require_source_runtime():
    if not _SUPPORTED_RUNTIME:
        raise UnsupportedExactSource("source_runtime_unsupported")
