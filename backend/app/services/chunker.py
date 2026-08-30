"""Chunker — splits a document's text into overlapping, embeddable segments.

Chunking is measured in *tokens*, not characters, because tokens are the unit
the embedding model actually limits.  A character-based split drifts against
the real token count and can silently overflow the model on dense text.

Boundaries are chosen with a preference order — paragraph, then sentence, then
a hard token cut — so a chunk ends at a natural break whenever one is close
enough.  Consecutive chunks overlap by a fixed number of tokens so a sentence
spanning a boundary still appears whole in one of them.
"""

import re
from typing import Optional

import tiktoken

from app.schemas.chunk import Chunk
from app.services.provenance import document_id_for, vector_id_for

# Target size of a chunk, in tokens.  Small enough that a retrieved chunk is
# specific rather than a wall of text, large enough to hold a whole argument.
DEFAULT_CHUNK_SIZE = 512

# Tokens repeated from the end of the previous chunk into the next one.
DEFAULT_CHUNK_OVERLAP = 64

# Encoding used to count tokens.  cl100k_base backs every current OpenAI
# embedding model, so counts here match what the API will charge for.
DEFAULT_ENCODING = "cl100k_base"

# How far back from a hard cut we will look for a natural boundary, as a
# fraction of the chunk.  Beyond this the boundary is too far back to be worth
# the shrunken chunk, so we take the hard cut instead.
BOUNDARY_SEARCH_FRACTION = 0.25

# Paragraph break: a blank line, however much trailing whitespace it carries.
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

# Sentence end: terminal punctuation followed by whitespace.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s")

# Encoders are expensive to build and safe to share, so keep one per encoding.
_encoders: dict[str, tiktoken.Encoding] = {}


def _get_encoder(encoding_name: str) -> tiktoken.Encoding:
    """Return a cached token encoder for the given encoding name."""
    if encoding_name not in _encoders:
        _encoders[encoding_name] = tiktoken.get_encoding(encoding_name)
    return _encoders[encoding_name]


def count_tokens(text: str, encoding_name: str = DEFAULT_ENCODING) -> int:
    """Count the tokens in a string.

    Args:
        text: The text to measure.
        encoding_name: Which tiktoken encoding to measure against.

    Returns:
        The number of tokens the embedding model will see.
    """
    return len(_get_encoder(encoding_name).encode(text))


def _find_boundary(text: str, earliest: int) -> Optional[int]:
    """Find the best natural break in `text` at or after `earliest`.

    Prefers the last paragraph break, falling back to the last sentence end.
    Searching from `earliest` onwards keeps the chunk close to its target size
    rather than truncating it back to the first break in the text.

    Args:
        text: The candidate chunk's text.
        earliest: Character offset before which a break is not worth taking.

    Returns:
        The character offset to cut at, or None if there is no usable break.
    """
    for pattern in (_PARAGRAPH_BREAK, _SENTENCE_BREAK):
        # Take the *last* match, so the chunk stays as full as possible.
        matches = [m for m in pattern.finditer(text) if m.end() >= earliest]
        if matches:
            return matches[-1].end()
    return None


def split_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    encoding_name: str = DEFAULT_ENCODING,
) -> list[tuple[str, int, int]]:
    """Split text into overlapping segments sized by token count.

    Args:
        text: The full document text.
        chunk_size: Maximum tokens per chunk.
        chunk_overlap: Tokens repeated between consecutive chunks.
        encoding_name: Which tiktoken encoding to measure against.

    Returns:
        One tuple per chunk of (content, start_offset, end_offset), where the
        offsets are character positions in the original text.

    Raises:
        ValueError: If the overlap is not smaller than the chunk size, which
            would make the splitter loop forever.
    """
    if chunk_overlap >= chunk_size:
        raise ValueError(
            f"chunk_overlap ({chunk_overlap}) must be smaller than "
            f"chunk_size ({chunk_size}); otherwise chunking cannot advance."
        )

    stripped = text.strip()
    if not stripped:
        return []

    encoder = _get_encoder(encoding_name)
    tokens = encoder.encode(stripped)

    # Text short enough to embed whole needs no splitting at all.
    if len(tokens) <= chunk_size:
        return [(stripped, 0, len(stripped))]

    chunks: list[tuple[str, int, int]] = []
    # Character offset in `stripped` where the next chunk begins.
    cursor = 0
    # Token offset, tracked separately so overlap is measured in tokens.
    token_cursor = 0

    while token_cursor < len(tokens):
        window = tokens[token_cursor : token_cursor + chunk_size]
        candidate = encoder.decode(window)

        # The final window runs to the end of the text; take it as-is rather
        # than trimming it back to a boundary and dropping the remainder.
        is_last = token_cursor + chunk_size >= len(tokens)
        if is_last:
            content = candidate
        else:
            earliest = int(len(candidate) * (1 - BOUNDARY_SEARCH_FRACTION))
            boundary = _find_boundary(candidate, earliest)
            content = candidate[:boundary] if boundary else candidate

        content = content.strip()
        if content:
            # Offsets describe where this chunk sits in the original text, so
            # a caller can highlight the passage in the source file.
            start = stripped.find(content, cursor)
            start = start if start != -1 else cursor
            chunks.append((content, start, start + len(content)))

        if is_last:
            break

        # Advance by the consumed text minus the overlap.  Measuring the step
        # in tokens keeps the overlap exact even when the boundary search
        # trimmed the chunk well short of chunk_size.
        consumed = len(encoder.encode(content)) or chunk_size
        step = max(1, consumed - chunk_overlap)
        token_cursor += step
        cursor = len(encoder.decode(tokens[:token_cursor]))

    return chunks


def chunk_document(
    source_key: str,
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    encoding_name: str = DEFAULT_ENCODING,
) -> list[Chunk]:
    """Split a source file's text into Chunk records ready for embedding.

    Args:
        source_key: The object key the text came from.
        text: The extracted text of the whole file.
        chunk_size: Maximum tokens per chunk.
        chunk_overlap: Tokens repeated between consecutive chunks.
        encoding_name: Which tiktoken encoding to measure against.

    Returns:
        Chunks in document order, each carrying its position and offsets.
    """
    document_id = document_id_for(source_key)

    return [
        Chunk(
            # The chunk id *is* the vector id — one identity across both, so a
            # retrieved chunk can be traced straight back to its source file.
            id=vector_id_for(document_id, index),
            document_id=document_id,
            content=content,
            chunk_index=index,
            overlap=chunk_overlap if index else 0,
            start_offset=start,
            end_offset=end,
            char_count=len(content),
        )
        for index, (content, start, end) in enumerate(
            split_text(text, chunk_size, chunk_overlap, encoding_name)
        )
    ]
