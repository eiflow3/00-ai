"""Token measurement — the one place text is counted and cut in token space.

Chunk size is measured in *tokens*, never characters, because tokens are the
unit the embedding model actually limits.  A character budget drifts against
the real count: dense text (tables, code, CJK, long identifiers) tokenises far
more heavily than prose, so a character-sized chunk can silently overflow the
model on exactly the documents most likely to contain one.

Every strategy in this package measures through here, so two strategies can
never disagree about how long a passage is — which is what makes a comparison
between them mean anything.
"""

import tiktoken

# Encoding used to count tokens.  cl100k_base backs every current OpenAI
# embedding model, so counts here match what the API will charge for.
DEFAULT_ENCODING = "cl100k_base"

# Target size of a chunk, in tokens.  Small enough that a retrieved chunk is
# specific rather than a wall of text, large enough to hold a whole argument.
DEFAULT_CHUNK_SIZE = 512

# Tokens repeated from the end of the previous chunk into the next one.
DEFAULT_CHUNK_OVERLAP = 64

# Encoders are expensive to build and safe to share, so keep one per encoding.
_encoders: dict[str, tiktoken.Encoding] = {}


def get_encoder(encoding_name: str = DEFAULT_ENCODING) -> tiktoken.Encoding:
    """Return a cached token encoder for the given encoding name.

    Args:
        encoding_name: Which tiktoken encoding to use.

    Returns:
        The shared encoder for that encoding.
    """
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
    return len(get_encoder(encoding_name).encode(text))


def truncate_to_tokens(
    text: str, limit: int, encoding_name: str = DEFAULT_ENCODING
) -> str:
    """Cut a string back to at most `limit` tokens.

    The blunt fallback for a passage no natural boundary could shorten — one
    unbroken paragraph longer than the whole chunk budget, most often.  Decoding
    a partial token window can leave a replacement character where a multi-byte
    character was cut; that is accepted here as the price of never handing the
    embedding model more than it accepts.

    Args:
        text: The text to shorten.
        limit: Maximum tokens to keep.
        encoding_name: Which tiktoken encoding to measure against.

    Returns:
        The text, returned unchanged when it already fits.
    """
    encoder = get_encoder(encoding_name)
    tokens = encoder.encode(text)
    if len(tokens) <= limit:
        return text
    return encoder.decode(tokens[:limit])
