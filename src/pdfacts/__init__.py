"""pdfacts: structured, verifiable facts from PDF files."""

from pdfacts.annotations.model import Annotation
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
from pdfacts.core.recovery import Recovery, recover
from pdfacts.core.refs import AnnotationRef, FieldRef, PageRef
from pdfacts.core.runtime import FontPolicy, configure
from pdfacts.core.strings import Name, NameEncoding
from pdfacts.core.values import Unknown, UnknownReason
from pdfacts.forms.model import (
    BorderStyle,
    ChoiceOption,
    DeviceColour,
    Field,
    FieldType,
    Widget,
)
from pdfacts.forms.reader import fields, widgets

__version__ = "0.0.1"

__all__ = [
    "Annotation",
    "AnnotationRef",
    "Bitmap",
    "BorderStyle",
    "Capability",
    "ChoiceOption",
    "ClosedError",
    "Colour",
    "ConfigurationError",
    "DamagedFileError",
    "DeviceColour",
    "Document",
    "EmptyInputError",
    "EngineError",
    "EngineInfo",
    "Field",
    "FieldRef",
    "FieldType",
    "FontPolicy",
    "InputError",
    "LimitError",
    "LimitExceededError",
    "LimitUncheckedError",
    "Limits",
    "Matrix",
    "Name",
    "NameEncoding",
    "NotPdfError",
    "Page",
    "PageGeometry",
    "PageRef",
    "PasswordRequiredError",
    "PdfactsError",
    "Point",
    "Recovery",
    "RecoveryFailedError",
    "Rect",
    "Unknown",
    "UnknownReason",
    "UnsupportedError",
    "Widget",
    "__version__",
    "capabilities",
    "configure",
    "engine_info",
    "fields",
    "open",
    "recover",
    "require",
    "widgets",
]
