"""pdfacts: structured, verifiable facts from PDF files."""

from pdfacts.core.bitmap import Bitmap, Colour
from pdfacts.core.capabilities import Capability, capabilities, require
from pdfacts.core.document import Document, Page, open
from pdfacts.core.engines import EngineInfo, engine_info
from pdfacts.core.errors import (
    ClosedError,
    ConfigurationError,
    DamagedFileError,
    EmptyInputError,
    EngineError,
    InputError,
    LimitError,
    LimitExceededError,
    LimitUncheckedError,
    NotPdfError,
    PasswordRequiredError,
    PdfactsError,
    RecoveryFailedError,
    UnsupportedError,
)
from pdfacts.core.geometry import Matrix, PageGeometry, Point, Rect
from pdfacts.core.limits import Limits
from pdfacts.core.refs import PageRef
from pdfacts.core.runtime import FontPolicy, configure
from pdfacts.core.values import Unknown, UnknownReason

__version__ = "0.0.1"

__all__ = [
    "Bitmap",
    "Capability",
    "ClosedError",
    "Colour",
    "ConfigurationError",
    "DamagedFileError",
    "Document",
    "EmptyInputError",
    "EngineError",
    "EngineInfo",
    "FontPolicy",
    "InputError",
    "LimitError",
    "LimitExceededError",
    "LimitUncheckedError",
    "Limits",
    "Matrix",
    "NotPdfError",
    "Page",
    "PageGeometry",
    "PageRef",
    "PasswordRequiredError",
    "PdfactsError",
    "Point",
    "RecoveryFailedError",
    "Rect",
    "Unknown",
    "UnknownReason",
    "UnsupportedError",
    "__version__",
    "capabilities",
    "configure",
    "engine_info",
    "open",
    "require",
]
