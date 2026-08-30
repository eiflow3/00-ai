"""Text extraction — turns a source file's raw bytes into plain text.

Only the formats we can read reliably are accepted.  Anything else raises
`UnsupportedSourceType`, which the pipeline reports as a per-file status rather
than letting one stray upload abort a whole ingestion run.

New formats are added by registering an extractor here — never by branching at
a call site.
"""

from pathlib import PurePosixPath
from typing import Callable

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


def _extract_plain_text(data: bytes) -> str:
    """Decode bytes as text, trying each supported encoding in turn."""
    for encoding in _TEXT_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    # latin-1 maps every byte, so reaching here means the file is not text.
    raise UnsupportedSourceType(".bin")


# Extension -> extractor.  Adding a format means adding a row here and its
# dependency; no other module needs to know the difference.
_EXTRACTORS: dict[str, Callable[[bytes], str]] = {
    ".txt": _extract_plain_text,
    ".md": _extract_plain_text,
    ".markdown": _extract_plain_text,
}

SUPPORTED_EXTENSIONS = frozenset(_EXTRACTORS)


def is_supported(source_key: str) -> bool:
    """Whether this pipeline can read the file at `source_key`.

    Args:
        source_key: The object key within the bucket.

    Returns:
        True if an extractor is registered for the key's extension.
    """
    return PurePosixPath(source_key).suffix.lower() in _EXTRACTORS


def extract_text(source_key: str, data: bytes) -> str:
    """Extract plain text from a source file's bytes.

    Args:
        source_key: The object key, used only to pick an extractor.
        data: The file's raw contents.

    Returns:
        The file's text content.

    Raises:
        UnsupportedSourceType: If the key's extension has no extractor.
    """
    extension = PurePosixPath(source_key).suffix.lower()

    extractor = _EXTRACTORS.get(extension)
    if extractor is None:
        raise UnsupportedSourceType(extension)

    return extractor(data)
