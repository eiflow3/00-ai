"""Catalog of the models this deployment can actually use.

The chat endpoint accepts a provider and a model, but not every combination is
usable: a provider needs credentials, and a model needs an entry in the pricing
registry or its cost reports as zero. Rather than have each client hardcode a
list that drifts, the catalog answers what is available here, right now.
"""

from app.config import settings
from app.services.cost_tracker import PRICING
from app.services.llm.factory import supported_providers

# One default model per provider — the one the chat endpoint falls back to.
DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-5.6-terra",
    "claude": "claude-sonnet-5",
}

# Human-readable names, so a client does not have to prettify model ids.
PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "claude": "Claude",
}

MODEL_LABELS: dict[str, str] = {
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "claude-sonnet-5": "Claude Sonnet 5",
}


def _availability(provider: str) -> tuple[bool, str]:
    """Whether a provider is usable here, and what is missing if not.

    Args:
        provider: The provider key.

    Returns:
        Whether it can be used, and a note explaining any gap. The note is
        shown to a person, so it names the setting they need to fill in.
    """
    if provider == "openai":
        if not settings.openai_api_key:
            return False, "OPENAI_API_KEY is not set."
        return True, ""

    if provider == "claude":
        if not settings.anthropic_api_key:
            return False, "ANTHROPIC_API_KEY is not set."
        # Whether the key also needs a workspace id cannot be told apart from a
        # working one without calling the API, so nothing is warned about here.
        # If it does, the request fails and the generation error says so.
        return True, ""

    return False, "No availability rule for this provider."


def list_models() -> list[dict]:
    """List every provider and model this deployment offers.

    Returns:
        One entry per usable provider/model pair, in registry order, each
        carrying its labels, whether it is available, and any caveat.
    """
    entries: list[dict] = []

    for provider in supported_providers():
        model = DEFAULT_MODELS.get(provider)
        if model is None:
            continue

        available, detail = _availability(provider)

        entries.append(
            {
                "provider": provider,
                "provider_label": PROVIDER_LABELS.get(provider, provider),
                "model": model,
                "model_label": MODEL_LABELS.get(model, model),
                "available": available,
                "detail": detail,
                # A model with no pricing entry reports zero cost rather than
                # failing, which is worth surfacing rather than hiding.
                "priced": model in PRICING,
            }
        )

    return entries
