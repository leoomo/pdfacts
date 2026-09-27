"""pdfacts: structured, verifiable facts from PDF files."""

from pdfacts.core.capabilities import Capability, capabilities, require
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
from pdfacts.core.values import Unknown, UnknownReason

__version__ = "0.0.1"

__all__ = [
    "Capability",
    "ClosedError",
    "ConfigurationError",
    "DamagedFileError",
    "EmptyInputError",
    "EngineError",
    "EngineInfo",
    "InputError",
    "LimitError",
    "LimitExceededError",
    "LimitUncheckedError",
    "Matrix",
    "NotPdfError",
    "PageGeometry",
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
    "engine_info",
    "require",
]
