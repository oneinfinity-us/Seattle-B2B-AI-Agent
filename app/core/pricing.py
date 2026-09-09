"""
Rough per-model USD pricing for cost *estimation* — good enough for a relative "how much did this
cost" story and an aggregate spend number, not for billing reconciliation. Verify against Anthropic's
current pricing page before treating these as exact; model pricing changes over time.
"""
from __future__ import annotations

# {model: (usd per 1M input tokens, usd per 1M output tokens)}
_PRICING_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-6": (0.80, 4.0),
}
_DEFAULT_PRICING = (3.0, 15.0)  # Sonnet-tier rate, used for a model not in the table above


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    input_rate, output_rate = _PRICING_PER_MILLION_TOKENS.get(model, _DEFAULT_PRICING)
    return (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate
