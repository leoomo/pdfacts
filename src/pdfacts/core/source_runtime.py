"""Typed feature boundary for the private source-proof runtime restriction."""

from pdfacts.backends.exact_stream import UnsupportedExactSource
from pdfacts.backends.source_runtime import require_source_runtime as require_backend_runtime
from pdfacts.core.errors import SourceEvidenceError


def require_source_runtime():
    try:
        require_backend_runtime()
    except UnsupportedExactSource as failure:
        raise SourceEvidenceError(reason=str(failure)) from failure
