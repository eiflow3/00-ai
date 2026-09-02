"""Text extraction — turns a source file's raw bytes into canonical text.

Only the formats we can read reliably are accepted.  Anything else raises
`UnsupportedSourceType`, which the pipeline reports as a per-file status rather
than letting one stray upload abort a whole ingestion run.

New formats are added by registering an extractor here — never by branching at
a call site.

Two kinds of format, one registry.  A plain-text file *is* its text, so its
extractor just decodes bytes.  A structured format — a PDF — has to be
normalised first (reading order, OCR, page markers, tables), which is slow and
therefore runs once per file version, with the result stored durably as a
derived artifact (see app.services.derived_artifacts).  `requires_derived_artifact`
is how the rest of the pipeline tells the two apart without naming extensions.
"""

from pathlib import PurePosixPath
from typing import Callable

from app.schemas.extraction import ExtractionResult

# Encodings tried in order when decoding a text file.  utf-8-sig leads because
# it reads plain UTF-8 identically while also stripping a byte-order mark,
# which would otherwise survive into the first chunk as a stray character.
# The rest rescue files exported from older Windows tooling instead of
# failing a whole document on one bad byte.
_TEXT_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


class UnsupportedSourceType(ValueError):
    """Raised when a file's extension has no registered extractor.

    Carries the extension so the caller can report exactly what was skipped.
    """

    def __init__(self, extension: str) -> None:
        self.extension = extension
        super().__init__(
            f"No extractor for {extension or 'files without an extension'!r}. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )


def _extract_plain_text(data: bytes, source_key: str = "") -> ExtractionResult:
    """Decode bytes as text, trying each supported encoding in turn."""
    for encoding in _TEXT_ENCODINGS:
        try:
            return ExtractionResult(text=data.decode(encoding))
        except UnicodeDecodeError:
            continue

    # latin-1 maps every byte, so reaching here means the file is not text.
    raise UnsupportedSourceType(".bin")


def _extract_pdf(data: bytes, source_key: str = "") -> ExtractionResult:
    """Hand a PDF to Docling, resolved at call time.

    Late-bound on purpose, twice over: the Docling stack is heavy enough that a
    process which never meets a PDF should not import it, and the test suite
    fakes `pdf_extraction.extract_pdf` at this seam the way it fakes the other
    vendors.
    """
    from app.services import pdf_extraction

    return pdf_extraction.extract_pdf(data, source_key)


# Extension -> extractor.  Adding a format means adding a row here and its
# dependency; no other module needs to know the difference.
_EXTRACTORS: dict[str, Callable[[bytes, str], ExtractionResult]] = {
    ".txt": _extract_plain_text,
    ".md": _extract_plain_text,
    ".markdown": _extract_plain_text,
    ".pdf": _extract_pdf,
}

SUPPORTED_EXTENSIONS = frozenset(_EXTRACTORS)

# Formats whose extraction is expensive and structured, so its result is
# persisted as a derived artifact and read back from there ever after.
DERIVED_EXTENSIONS = frozenset({".pdf"})


def is_supported(source_key: str) -> bool:
    """Whether this pipeline can read the file at `source_key`.

    Args:
        source_key: The object key within the bucket.

    Returns:
        True if an extractor is registered for the key's extension.
    """
    return PurePosixPath(source_key).suffix.lower() in _EXTRACTORS


def requires_derived_artifact(source_key: str) -> bool:
    """Whether this file's text lives in a stored derived artifact.

    True for formats whose extraction is too expensive to repeat on every
    read — their canonical text is produced at index time and stored, and
    read-back paths must go through app.services.derived_artifacts.

    Args:
        source_key: The object key within the bucket.

    Returns:
        True if the format's extraction result is persisted rather than
        recomputed per read.
    """
    return PurePosixPath(source_key).suffix.lower() in DERIVED_EXTENSIONS


def extract_document(source_key: str, data: bytes) -> ExtractionResult:
    """Extract a source file's canonical text and structure.

    CPU-bound for structured formats — a PDF takes seconds to minutes — so
    callers on the event loop must wrap this in `asyncio.to_thread`.

    Args:
        source_key: The object key, used to pick an extractor.
        data: The file's raw contents.

    Returns:
        The canonical text, plus pages and tables where the format has them.

    Raises:
        UnsupportedSourceType: If the key's extension has no extractor.
    """
    extension = PurePosixPath(source_key).suffix.lower()

    extractor = _EXTRACTORS.get(extension)
    if extractor is None:
        raise UnsupportedSourceType(extension)

    return extractor(data, source_key)


def extract_text(source_key: str, data: bytes) -> str:
    """Extract plain text from a source file's bytes.

    The text-only view of `extract_document`, kept for callers that have no
    use for pages or tables.

    Args:
        source_key: The object key, used only to pick an extractor.
        data: The file's raw contents.

    Returns:
        The file's canonical text.

    Raises:
        UnsupportedSourceType: If the key's extension has no extractor.
    """
    return extract_document(source_key, data).text
