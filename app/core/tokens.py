"""Token estimation and cost accounting."""

from __future__ import annotations

import json

from app.llm.base import Usage

# USD per million tokens: (input, output, cache_read). Cache writes are billed at 1.25x input.
# Figures are estimates for budgeting, not billing; check current pricing before relying on them.
# Override or extend with LLM_PRICE_OVERRIDES='{"model": [in, out, cache_read]}'.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}

CHARS_PER_TOKEN = 3.5
SAFETY_MARGIN = 1.15


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN * SAFETY_MARGIN) + 1


def load_overrides(raw: str) -> None:
    if not raw:
        return
    data = json.loads(raw)
    for model, triple in data.items():
        PRICES[model] = (float(triple[0]), float(triple[1]), float(triple[2]))


def cost_usd(model: str, usage: Usage) -> float:
    """Estimated cost. Unknown models (local, custom) cost 0."""
    price = PRICES.get(model)
    if price is None:
        # Tolerate provider-returned ids with suffixes, e.g. "claude-opus-5-5-xyz".
        price = next((p for m, p in PRICES.items() if model.startswith(m + "-")), None)
    if price is None:
        return 0.0
    p_in, p_out, p_cache = price
    return (
        usage.input_tokens * p_in
        + usage.output_tokens * p_out
        + usage.cache_read_tokens * p_cache
        + usage.cache_write_tokens * p_in * 1.25
    ) / 1_000_000
