"""Cost tracking module — calculates per-request token costs based on model pricing.

Pricing data sourced from official provider pages (as of August 2026):
- OpenAI: https://openai.com/api/pricing
- Anthropic: https://docs.anthropic.com/en/docs/about-claude/models
"""

from dataclasses import dataclass


@dataclass
class CostBreakdown:
    """Detailed cost breakdown for a single LLM request."""
    # Token counts from the provider's usage response.
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    # Calculated dollar costs for each token category.
    input_cost: float = 0.0
    output_cost: float = 0.0
    cache_read_cost: float = 0.0
    cache_write_cost: float = 0.0

    # Sum of all costs.
    total_cost: float = 0.0

    # The model that was used.
    model: str = ""

    def to_dict(self) -> dict:
        """Serialize to a dict for the SSE usage event."""
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "input_cost": round(self.input_cost, 8),
            "output_cost": round(self.output_cost, 8),
            "cache_read_cost": round(self.cache_read_cost, 8),
            "cache_write_cost": round(self.cache_write_cost, 8),
            "total_cost": round(self.total_cost, 8),
        }


# Pricing registry — cost per 1 million tokens for each model.
# Add new models here as they become available.
# Source: Official provider pricing pages (August 2026).
PRICING: dict[str, dict[str, float]] = {
    # OpenAI GPT-5.6 Terra (mid-tier, balanced model)
    # https://openai.com/api/pricing
    "gpt-5.6-terra": {
        "input_per_1m": 2.00,
        "output_per_1m": 12.00,
        "cache_read_per_1m": 0.20,
        "cache_write_per_1m": 2.50,
    },
    # Anthropic Claude Sonnet 5 (permanent standard rate)
    # https://docs.anthropic.com/en/docs/about-claude/models
    "claude-sonnet-5-latest": {
        "input_per_1m": 2.00,
        "output_per_1m": 10.00,
        "cache_read_per_1m": 0.20,
        "cache_write_per_1m": 2.50,  # 5-minute TTL default
    },
}


def calculate_cost(model: str, usage: dict) -> CostBreakdown:
    """Calculate the dollar cost of a request based on token usage.

    Args:
        model: The model name used for the request (e.g. "gpt-5.6-terra").
        usage: A dict with keys: input_tokens, output_tokens,
               cache_read_tokens, cache_write_tokens.

    Returns:
        A CostBreakdown with individual and total costs.
        If the model isn't in the pricing registry, costs will be 0.
    """
    # Look up the pricing for this model; default to zeros if unknown.
    prices = PRICING.get(model, {})

    # Extract token counts from the usage dict, defaulting to 0.
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_read_tokens = usage.get("cache_read_tokens", 0)
    cache_write_tokens = usage.get("cache_write_tokens", 0)

    # Calculate cost for each category: (tokens / 1,000,000) * price_per_1m.
    input_cost = (input_tokens / 1_000_000) * prices.get("input_per_1m", 0)
    output_cost = (output_tokens / 1_000_000) * prices.get("output_per_1m", 0)
    cache_read_cost = (cache_read_tokens / 1_000_000) * prices.get("cache_read_per_1m", 0)
    cache_write_cost = (cache_write_tokens / 1_000_000) * prices.get("cache_write_per_1m", 0)

    return CostBreakdown(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        input_cost=input_cost,
        output_cost=output_cost,
        cache_read_cost=cache_read_cost,
        cache_write_cost=cache_write_cost,
        total_cost=input_cost + output_cost + cache_read_cost + cache_write_cost,
    )
