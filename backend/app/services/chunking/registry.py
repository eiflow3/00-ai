"""Registry mapping a strategy name to the function that implements it.

The whole extension point.  Adding a way of cutting documents is a new module
beside this one, a member on `ChunkStrategy`, and one line here — never a
branch at a call site, and never a keyword argument that only one caller passes.

The completeness check below runs at import: a strategy offered through the API
with nothing behind it would be a 500 the first time somebody picked it, and
finding that out when the process starts is better than finding it out from a
user.
"""

from app.schemas.chunking import ChunkStrategy
from app.services.chunking import boundary, fixed, recursive, structural
from app.services.chunking.base import SplitFunction, UnknownStrategy

# Every strategy the pipeline can cut with.  Registration order is the order a
# client is offered them, so the baseline comes first and the floor second.
_STRATEGIES: dict[ChunkStrategy, SplitFunction] = {
    ChunkStrategy.BOUNDARY: boundary.split,
    ChunkStrategy.FIXED: fixed.split,
    ChunkStrategy.RECURSIVE: recursive.split,
    ChunkStrategy.STRUCTURAL: structural.split,
}

# A member of the enum with no implementation would reach the API as an offer
# nothing can honour. Fail at import instead, where it is unmissable.
_unimplemented = [strategy.value for strategy in ChunkStrategy if strategy not in _STRATEGIES]
if _unimplemented:
    raise RuntimeError(
        "Chunking strategies declared but not implemented: "
        f"{', '.join(_unimplemented)}. Add them to _STRATEGIES in "
        "app.services.chunking.registry."
    )


def strategies() -> list[ChunkStrategy]:
    """List the strategies the registry knows, in the order to offer them."""
    return list(_STRATEGIES)


def get(strategy: ChunkStrategy) -> SplitFunction:
    """Return the split function for a strategy.

    Args:
        strategy: Which strategy to use.

    Returns:
        The strategy's split function.

    Raises:
        UnknownStrategy: If nothing is registered under that name. Raised
            rather than silently falling back to the default, because a caller
            who asked for one way of cutting and quietly got another would
            compare two variants that are secretly the same.
    """
    split = _STRATEGIES.get(strategy)
    if split is None:
        known = ", ".join(name.value for name in _STRATEGIES)
        raise UnknownStrategy(f"Unknown chunking strategy: {strategy!r}. Known: {known}")
    return split
