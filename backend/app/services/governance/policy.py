"""Policy resolution: request > source > global default, in one place.

A call site hands over whatever layers it has and gets back one resolved
policy — it never reasons about layering itself.  Two rules:

  * A layer overrides only the fields it explicitly set (pydantic's
    `model_fields_set`), so a source policy that names just a mode does not
    silently reset the domain allowlist underneath it.
  * The request layer is additionally clipped to
    `GovernancePolicy.request_overridable_fields()` — which is how the
    verbatim knob can never arrive from a request body.
"""

from typing import Optional

from app.schemas.governance import GovernancePolicy


def resolve(
    default: GovernancePolicy,
    source: Optional[GovernancePolicy] = None,
    request: Optional[GovernancePolicy] = None,
) -> GovernancePolicy:
    """Merge the layers into the one policy a governance run uses."""
    merged = default.model_copy(deep=True)
    if source is not None:
        merged = merged.model_copy(
            update={name: getattr(source, name) for name in source.model_fields_set}
        )
    if request is not None:
        allowed = GovernancePolicy.request_overridable_fields() & request.model_fields_set
        merged = merged.model_copy(
            update={name: getattr(request, name) for name in allowed}
        )
    return merged
