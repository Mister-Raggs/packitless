"""Extractor registry and auto-selection.

Selection is a competition, not a guess: every registered extractor sniffs the
payload and reports how well it expects to do, and the highest scorer wins.
An unfamiliar format therefore degrades to whichever extractor genuinely finds
redundancy, rather than to an error.
"""

from __future__ import annotations

import logging

from packitless.extractors.base import Extractor, Group, Structure
from packitless.extractors.hybrid import HybridExtractor
from packitless.extractors.jsonrec import JsonRecordExtractor
from packitless.extractors.lines import LineTemplateExtractor
from packitless.types import Record

logger = logging.getLogger(__name__)

# Confidence below this means no extractor found meaningful structure.
MIN_CONFIDENCE = 0.05

_SPECIALISTS: list[Extractor] = [
    LineTemplateExtractor(),
    JsonRecordExtractor(),
]

REGISTRY: dict[str, Extractor] = {
    ex.name: ex for ex in _SPECIALISTS
}
# The router competes on heterogeneous payloads only; it returns 0.0 when one
# specialist claims nearly everything, so homogeneous payloads never pay for
# a wrapper they do not need.
REGISTRY["hybrid"] = HybridExtractor(_SPECIALISTS)


def register(extractor: Extractor) -> None:
    """Add an extractor to the registry."""
    REGISTRY[extractor.name] = extractor


def sniff_all(records: list[Record]) -> list[tuple[str, float]]:
    """Score every extractor against `records`, best first."""
    scores = [(name, ex.sniff(records)) for name, ex in REGISTRY.items()]
    return sorted(scores, key=lambda kv: kv[1], reverse=True)


def select(
    records: list[Record], prefer: str = "auto", require_lossless: bool = False
) -> tuple[Extractor, float]:
    """Choose an extractor for `records`.

    Args:
        records: The payload.
        prefer: An extractor name, or "auto" to run the sniff competition.
        require_lossless: Only consider extractors the payload can be rebuilt
            from. Without this the competition maximises ratio alone, and the
            winner is often lossy even when a reversible option was available.

    Returns:
        The chosen extractor and its confidence.

    Raises:
        KeyError: If `prefer` names an unregistered extractor.
    """
    if prefer != "auto":
        return REGISTRY[prefer], REGISTRY[prefer].sniff(records)

    scores = sniff_all(records)
    if require_lossless:
        scores = [
            (n, s) for n, s in scores
            if getattr(REGISTRY[n], "reconstructable", False)
        ]
        if not scores:
            logger.info("no reconstructable extractor available — passing through")
            return REGISTRY["lines"], 0.0
    name, confidence = scores[0]
    if confidence < MIN_CONFIDENCE:
        logger.info(
            "no extractor found structure (best: %s at %.3f) — payload is "
            "near-incompressible",
            name,
            confidence,
        )
    return REGISTRY[name], confidence


__all__ = [
    "Extractor",
    "Group",
    "MIN_CONFIDENCE",
    "REGISTRY",
    "Structure",
    "register",
    "select",
    "sniff_all",
]
