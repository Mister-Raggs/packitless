"""Turning tokens into money.

Tokens are the mechanism; dollars are the reason anyone cares. A percentage
with no rate attached is unfalsifiable — and a raw total is misleading in the
other direction, because a single call costs fractions of a cent. The useful
figure is a *rate*: cost per thousand invocations at a stated model price.

Rates are list prices in USD per million tokens, stated openly so the
arithmetic can be checked rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass

# USD per million tokens: (input, output). List prices.
RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

DEFAULT_MODEL = "claude-sonnet-5"


@dataclass
class Savings:
    """What compression is worth, at a stated rate and call volume.

    Attributes:
        model: Which price list was applied.
        input_rate: USD per million input tokens.
        tokens_before: Input tokens per call without compression.
        tokens_after: Input tokens per call with it.
        calls: How many calls the projection covers.
        cost_before: USD for `calls` uncompressed calls.
        cost_after: USD for `calls` compressed calls.
    """

    model: str
    input_rate: float
    tokens_before: int
    tokens_after: int
    calls: int
    cost_before: float
    cost_after: float

    @property
    def saved(self) -> float:
        return self.cost_before - self.cost_after

    @property
    def saved_pct(self) -> float:
        if self.cost_before == 0:
            return 0.0
        return 100.0 * self.saved / self.cost_before


def input_cost(tokens: int, model: str = DEFAULT_MODEL, calls: int = 1) -> float:
    """USD for `tokens` input tokens across `calls` calls."""
    rate, _ = RATES.get(model, RATES[DEFAULT_MODEL])
    return tokens / 1_000_000 * rate * calls


def project(
    tokens_before: int,
    tokens_after: int,
    model: str = DEFAULT_MODEL,
    calls: int = 1_000,
) -> Savings:
    """Project the saving of compressing one payload across `calls` calls.

    Only input tokens are modelled. Compression changes what you send, not
    what comes back, so counting output savings would be claiming credit for
    something that did not happen.
    """
    rate, _ = RATES.get(model, RATES[DEFAULT_MODEL])
    return Savings(
        model=model,
        input_rate=rate,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        calls=calls,
        cost_before=input_cost(tokens_before, model, calls),
        cost_after=input_cost(tokens_after, model, calls),
    )
