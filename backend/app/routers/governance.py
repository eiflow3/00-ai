"""GET /governance endpoints — read what content is screened under.

The stages themselves run inside the indexing and chat pipelines; this
router only exposes the resolved policy so a client can render defaults
honestly instead of hardcoding them.
"""

from fastapi import APIRouter

from app.docs.governance import GOVERNANCE_POLICY_DESCRIPTION
from app.schemas.governance import GovernancePolicy, GovernancePolicyView
from app.services.governance import policy as governance_policy
from app.services.governance.runner import STAGE_NAME

router = APIRouter()


@router.get(
    "/governance/policy",
    response_model=GovernancePolicyView,
    summary="Read the resolved global governance policy",
    response_description="The policy every run uses when a request sends nothing.",
    description=GOVERNANCE_POLICY_DESCRIPTION,
    tags=["governance"],
)
def get_policy() -> GovernancePolicyView:
    """The deployment's governance defaults, and what a request may override.

    Returns:
        The resolved policy: mode, verbatim capture level, domain allowlist,
        per-class actions, the request-overridable field names, and which
        governance stages this deployment runs.
    """
    policy = governance_policy.default_policy()
    return GovernancePolicyView(
        mode=policy.mode,
        verbatim=policy.verbatim,
        own_domains=policy.own_domains,
        actions=policy.actions,
        request_overridable=sorted(GovernancePolicy.request_overridable_fields()),
        stages=[STAGE_NAME],
    )
