"""Compression orchestrator.

The pipeline is fixed; the pieces are pluggable:

    records -> sniff format -> extract structure -> score salience
            -> allocate budget -> render

Selection is a competition (see extractors/__init__.py), so a payload the
library has never seen degrades to whichever extractor genuinely finds
redundancy — or, if none does, to passthrough.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from packitless import extractors
from packitless.allocator import allocate
from packitless.render import render
from packitless.salience import score_records
from packitless.tokens import TokenCounter, get_counter
from packitless.types import CompressedContext, Record

logger = logging.getLogger(__name__)


@dataclass
class CompressConfig:
    """One point in the compression design space.

    Attributes:
        name: Label used in reports.
        budget_tokens: Token ceiling for the rendered output. None means
            compress structurally without a hard cap.
        extractor: "auto" to run the sniff competition, or an explicit name.
        verbatim_floor: Records at or above this salience are preserved
            verbatim regardless of budget pressure. This is the guarantee that
            stops compression from eating the anomaly you were looking for.
        max_verbatim: Cap on verbatim records, so one noisy incident cannot
            consume the whole budget.
        min_confidence: If the best extractor scores below this, pass the
            payload through untouched rather than mangling it.
    """

    name: str
    budget_tokens: int | None = None
    extractor: str = "auto"
    verbatim_floor: float = 0.9
    max_verbatim: int = 10
    min_confidence: float = extractors.MIN_CONFIDENCE


PASSTHROUGH = CompressConfig(name="passthrough", extractor="passthrough")


def compress(
    records: list[Record],
    config: CompressConfig = PASSTHROUGH,
    counter: TokenCounter | None = None,
) -> CompressedContext:
    """Compress a payload under `config`.

    Args:
        records: The payload.
        config: Extractor choice, budget, and preservation guarantees.
        counter: Token counter used for budget decisions. Defaults to the
            best available.

    Returns:
        A CompressedContext whose `.text` is ready to drop into a prompt.
    """
    if not records:
        return CompressedContext(text="", records_in=0)

    if config.extractor == "passthrough":
        return _passthrough(records, reason="requested")

    counter = counter or get_counter()
    extractor, confidence = extractors.select(records, prefer=config.extractor)

    if confidence < config.min_confidence:
        return _passthrough(
            records,
            reason=f"no structure found (best {extractor.name} at {confidence:.3f})",
        )

    structure = extractor.extract(records)
    scores = score_records(records, structure)
    plan = allocate(
        records=records,
        structure=structure,
        scores=scores,
        counter=counter,
        budget_tokens=config.budget_tokens,
        verbatim_floor=config.verbatim_floor,
        max_verbatim=config.max_verbatim,
    )
    text = render(records, structure, plan, scores)

    dropped: list[str] = list(plan.notes)
    if plan.groups_omitted:
        dropped.append(f"{plan.groups_omitted} pattern(s) omitted")

    # A structure is only reconstructable in practice if the budget did not
    # force anything out of the rendered output.
    truncated = bool(plan.groups_omitted or plan.rows_omitted)
    reconstructable = structure.reconstructable and not truncated

    return CompressedContext(
        text=text,
        records_in=len(records),
        records_verbatim=len(plan.verbatim),
        groups=len(structure.groups),
        dropped=dropped,
        stats={
            "extractor": extractor.name,
            "confidence": round(confidence, 4),
            "estimated_ceiling": round(
                structure.compression_estimate(len(records)), 4
            ),
            "groups_rendered": len(plan.groups),
            "rows_rendered": len(plan.rows),
            "rows_omitted": plan.rows_omitted,
            "truncated": truncated,
            # "lossless" means the payload can be rebuilt from the output.
            # "lossy" means distribution and salient records survive but
            # per-event detail does not — that is the claim the judge tests.
            "guarantee": "lossless" if reconstructable else "lossy",
            "overrun": plan.overrun,
            "notes": structure.notes,
        },
    )


def _passthrough(records: list[Record], reason: str) -> CompressedContext:
    """The baseline: every record, verbatim, in order.

    This is what an application does today when it interpolates a payload
    straight into a prompt. It is also the safe fallback when no extractor
    finds structure — passing text through unchanged is always correct.
    """
    return CompressedContext(
        text="\n".join(r.raw for r in records),
        records_in=len(records),
        records_verbatim=len(records),
        groups=0,
        stats={"extractor": "passthrough", "reason": reason},
    )
