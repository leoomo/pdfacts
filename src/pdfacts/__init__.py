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
from pdfacts.core.geometry import Matrix, PageGeometry, Point, Quad, Rect
from pdfacts.core.limits import Limits
from pdfacts.core.recovery import Recovery, recover
from pdfacts.core.refs import AnnotationRef, FieldRef, GlyphRef, GraphicRef, ObjectRef, PageRef
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
from pdfacts.graphics.images import ColourSpace, Image
from pdfacts.graphics.paths import (
    Dash,
    DisplayColour,
    FillRule,
    LineCap,
    LineJoin,
    Path,
    Segment,
    SegmentKind,
    ShapeKind,
    Subpath,
)
from pdfacts.graphics.reader import images, paths
from pdfacts.inspection.active_content import (
    Action,
    ActionPlace,
    ActionTrigger,
    ActiveContent,
    EmbeddedFile,
)
from pdfacts.inspection.report import (
    Contents,
    ContentsForm,
    ContentsStream,
    CryptFilter,
    CryptMethod,
    Encryption,
    Inspection,
    ObjectCounts,
    PageContents,
    Permissions,
    inspect,
)
from pdfacts.text.glyphs import Glyph
from pdfacts.text.layout import Line, Word
from pdfacts.text.reader import glyphs, lines, words

__version__ = "0.0.1"

__all__ = [
    "Action",
    "ActionPlace",
    "ActionTrigger",
    "ActiveContent",
    "Annotation",
    "AnnotationRef",
    "Bitmap",
    "BorderStyle",
    "Capability",
    "ChoiceOption",
    "ClosedError",
    "Colour",
    "ColourSpace",
    "ConfigurationError",
    "Contents",
    "ContentsForm",
    "ContentsStream",
    "CryptFilter",
    "CryptMethod",
    "DamagedFileError",
    "Dash",
    "DeviceColour",
    "DisplayColour",
    "Document",
    "EmbeddedFile",
    "EmptyInputError",
    "Encryption",
    "EngineError",
    "EngineInfo",
    "Field",
    "FieldRef",
    "FieldType",
    "FillRule",
    "FontPolicy",
    "Glyph",
    "GlyphRef",
    "GraphicRef",
    "Image",
    "InputError",
    "Inspection",
    "LimitError",
    "LimitExceededError",
    "LimitUncheckedError",
    "Limits",
    "Line",
    "LineCap",
    "LineJoin",
    "Matrix",
    "Name",
    "NameEncoding",
    "NotPdfError",
    "ObjectCounts",
    "ObjectRef",
    "Page",
    "PageContents",
    "PageGeometry",
    "PageRef",
    "PasswordRequiredError",
    "Path",
    "PdfactsError",
    "Permissions",
    "Point",
    "Quad",
    "Recovery",
    "RecoveryFailedError",
    "Rect",
    "Segment",
    "SegmentKind",
    "ShapeKind",
    "Subpath",
    "Unknown",
    "UnknownReason",
    "UnsupportedError",
    "Widget",
    "Word",
    "__version__",
    "capabilities",
    "configure",
    "engine_info",
    "fields",
    "glyphs",
    "images",
    "inspect",
    "lines",
    "open",
    "paths",
    "recover",
    "require",
    "widgets",
    "words",
]
