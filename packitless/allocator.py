"""Budget allocation.

The interface a real system can adopt is "here are N tokens, spend them well"
— not "compress by 60%". Applications have context windows and cost ceilings;
they do not have compression-ratio targets.

Allocation runs in priority order:

    1. must-keep records (salience >= verbatim_floor) — the guarantee
    2. the structural spine: legend plus one summary line per group
    3. leftover budget on further verbatim records, most salient first

If even step 1 exceeds the budget, we overrun rather than drop a must-keep
record and report it. Silently discarding the thing the caller said was
critical would make every downstream number meaningless.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from packitless.extractors.base import Group, Structure
from packitless.tokens import TokenCounter
from packitless.types import Record

logger = logging.getLogger(__name__)


@dataclass
class Plan:
    """What the renderer should emit.

    Attributes:
        groups: Groups to summarise, largest first.
        groups_omitted: Groups that did not fit the budget.
        rows: Compact per-record rows that fit the budget.
        rows_omitted: Rows dropped for budget. Non-zero means records were
            discarded — always surfaced, never silent.
        verbatim: Record indices to reproduce in full, most salient first.
        overrun: True if must-keep records alone exceeded the budget.
        notes: Allocation decisions worth surfacing in a report.
    """

    groups: list[Group] = field(default_factory=list)
    groups_omitted: int = 0
    rows: list[tuple[int, str]] = field(default_factory=list)
    rows_omitted: int = 0
    verbatim: list[int] = field(default_factory=list)
    overrun: bool = False
    notes: list[str] = field(default_factory=list)


def _group_line_cost(group: Group, counter: TokenCounter) -> int:
    """Token cost of one rendered summary line."""
    return counter.count(f"[{group.label}] x{group.count:,}  {group.pattern}")


def allocate(
    records: list[Record],
    structure: Structure,
    scores: dict[int, float],
    counter: TokenCounter,
    budget_tokens: int | None,
    verbatim_floor: float = 0.9,
    max_verbatim: int = 10,
) -> Plan:
    """Decide what fits in `budget_tokens`.

    Args:
        records: The payload.
        structure: Extractor output.
        scores: Salience per record index.
        counter: Token counter used to measure candidate output.
        budget_tokens: Ceiling, or None for unbounded.
        verbatim_floor: Salience at or above which a record is must-keep.
        max_verbatim: Cap on verbatim records.

    Returns:
        A Plan the renderer can execute.
    """
    plan = Plan()
    by_index = {r.index: r for r in records}

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    must_keep = [i for i, s in ranked if s >= verbatim_floor][:max_verbatim]

    if budget_tokens is None:
        # No budget means highest fidelity, not fewest tokens: keep the full
        # spine, every row, and the most salient records up to the cap.
        plan.groups = list(structure.groups)
        plan.rows = list(structure.compact_rows)
        plan.verbatim = [i for i, s in ranked if s > 0.0][:max_verbatim]
        plan.notes.append(f"unbounded: {len(plan.verbatim)} verbatim record(s)")
        return plan

    spent = 0

    # 1. Must-keep records are non-negotiable.
    for index in must_keep:
        spent += counter.count(by_index[index].raw)
        plan.verbatim.append(index)
    if spent > budget_tokens:
        plan.overrun = True
        plan.notes.append(
            f"{len(must_keep)} must-keep record(s) cost {spent} tok, "
            f"over the {budget_tokens} tok budget — kept anyway"
        )
        logger.warning(plan.notes[-1])
        return plan

    # 2. The structural spine, largest groups first.
    for group in structure.groups:
        cost = _group_line_cost(group, counter)
        if spent + cost > budget_tokens:
            plan.groups_omitted = len(structure.groups) - len(plan.groups)
            break
        plan.groups.append(group)
        spent += cost

    if plan.groups_omitted:
        plan.notes.append(
            f"{plan.groups_omitted} smaller template(s) omitted for budget"
        )

    # 3. Compact per-record rows, for extractors whose savings come from
    #    cheaper encoding rather than from collapsing records away. Dropping
    #    these is dropping data, so any shortfall is reported.
    for position, (index, text) in enumerate(structure.compact_rows):
        cost = counter.count(text)
        if spent + cost > budget_tokens:
            plan.rows_omitted = len(structure.compact_rows) - position
            break
        plan.rows.append((index, text))
        spent += cost

    if plan.rows_omitted:
        plan.notes.append(
            f"{plan.rows_omitted:,} of {len(structure.compact_rows):,} records "
            f"dropped for budget — payload is truncated, not merely compressed"
        )
        logger.warning(plan.notes[-1])

    # 4. Leftover budget on additional verbatim records.
    chosen = set(plan.verbatim)
    for index, score in ranked:
        if len(plan.verbatim) >= max_verbatim:
            break
        if index in chosen or score <= 0.0:
            continue
        cost = counter.count(by_index[index].raw)
        if spent + cost > budget_tokens:
            continue
        plan.verbatim.append(index)
        chosen.add(index)
        spent += cost

    plan.notes.append(f"allocated {spent}/{budget_tokens} tok")
    return plan
