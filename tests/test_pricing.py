from __future__ import annotations

from app.core.pricing import estimate_cost_usd


def test_known_model_uses_its_own_rate():
    cost = estimate_cost_usd("claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 3.0 + 15.0


def test_unknown_model_falls_back_to_the_default_rate():
    cost = estimate_cost_usd("some-future-model-not-in-the-table", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 3.0 + 15.0


def test_zero_tokens_costs_nothing():
    assert estimate_cost_usd("claude-sonnet-4-6", input_tokens=0, output_tokens=0) == 0.0


def test_scales_linearly_with_token_count():
    cost = estimate_cost_usd("claude-sonnet-4-6", input_tokens=500_000, output_tokens=0)
    assert cost == 1.5
