"""pdfacts: structured, verifiable facts from PDF files."""

from importlib import metadata as _metadata

from pdfacts.annotation.model import Annotation, AnnotationAppearance
from pdfacts.annotation.reader import annotations
from pdfacts.core.bitmap import Bitmap, Colour
from pdfacts.core.capabilities import Capability, capabilities, require
from pdfacts.core.decryption import Decryption, decrypt
from pdfacts.core.document import Document, Page, Pages, open
from pdfacts.core.engines import EngineInfo, engine_info
from pdfacts.core.errors import (
    ClosedError,
    ConfigurationError,
    DamagedFileError,
    DecryptionFailedError,
    EmptyInputError,
    EngineError,
    InputError,
    LimitError,
    LimitExceededError,
    LimitUncheckedError,
    NotEncryptedError,
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
from pdfacts.edit import Decrypt, Edited, EditSession, Rewrite
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
from pdfacts.text.rules import TextRules

# The version of the installed distribution, which pyproject.toml gives.
__version__ = _metadata.version("pdfacts")

__all__ = [
    "Action",
    "ActionPlace",
    "ActionTrigger",
    "ActiveContent",
    "Annotation",
    "AnnotationAppearance",
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
    "Decrypt",
    "Decryption",
    "DecryptionFailedError",
    "DeviceColour",
    "DisplayColour",
    "Document",
    "EditSession",
    "Edited",
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
    "NotEncryptedError",
    "NotPdfError",
    "ObjectCounts",
    "ObjectRef",
    "Page",
    "PageContents",
    "PageGeometry",
    "PageRef",
    "Pages",
    "PasswordRequiredError",
    "Path",
    "PdfactsError",
    "Permissions",
    "Point",
    "Quad",
    "Recovery",
    "Rewrite",
    "RecoveryFailedError",
    "Rect",
    "Segment",
    "SegmentKind",
    "ShapeKind",
    "Subpath",
    "TextRules",
    "Unknown",
    "UnknownReason",
    "UnsupportedError",
    "Widget",
    "Word",
    "__version__",
    "capabilities",
    "annotations",
    "configure",
    "decrypt",
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
