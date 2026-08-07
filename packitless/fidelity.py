"""Deterministic fidelity metrics — measuring information loss without an LLM.

An LLM judge answers "is the summary good?", costs money, and returns a noisy
integer. The claim that actually needs defending is narrower and cheaper to
test: *did compression discard something that mattered?*

Three metrics, all free, all deterministic, all runnable in CI:

    count_conservation   do the rendered counts still add up to the payload?
                         Catches silent record dropping — the exact bug that
                         made the JSON path look 99.9% effective by deleting
                         the data.

    pattern_coverage     what fraction of distinct patterns survive into the
                         output? Answers "do we still know what kinds of
                         events happened", which is what a lossy tier
                         promises.

    critical_recall      of the records the salience scorer flagged as most
                         important, how many appear verbatim in the output?
                         This is the guarantee: compression may drop routine
                         repetition, never the anomaly you were looking for.

None of this replaces the judge for answering whether a downstream summary is
useful. It does mean the guarantee is enforced on every commit rather than
whenever someone is willing to spend four dollars.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from packitless.allocator import Plan
from packitless.extractors.base import Structure
from packitless.types import Record

# Identifier-shaped tokens: the specifics an operator would need to act on.
_ENTITY_RE = re.compile(
    r"blk_-?\d+"
    r"|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?"
    r"|\b[0-9a-fA-F]{8,}\b"
    r"|/[\w.\-/]{4,}"
)


@dataclass
class Fidelity:
    """What survived compression.

    Attributes:
        count_conservation: Rendered record counts divided by input records.
            1.0 means every record is still accounted for, even if collapsed
            into a pattern. Below 1.0 means records were dropped.
        pattern_coverage: Distinct patterns present in the output over
            distinct patterns found in the payload.
        critical_recall: Fraction of high-salience records reproduced
            verbatim. This is the guarantee the allocator is supposed to keep.
        critical_entities: Fraction of identifier-shaped tokens from
            high-salience records that appear anywhere in the output.
        n_critical: How many records counted as critical.
    """

    count_conservation: float
    pattern_coverage: float
    critical_recall: float
    critical_entities: float
    n_critical: int

    @property
    def passed(self) -> bool:
        """True when every guarantee held.

        Critical recall is the hard one: a compressor that drops a flagged
        record has failed regardless of how good its ratio looks.
        """
        return (
            self.count_conservation >= 0.999
            and self.critical_recall >= 0.999
            and self.pattern_coverage >= 0.999
        )


def entities(text: str) -> set[str]:
    """Identifier-shaped tokens in `text`."""
    return set(_ENTITY_RE.findall(text))


def measure(
    records: list[Record],
    structure: Structure,
    plan: Plan,
    scores: dict[int, float],
    rendered: str,
    verbatim_floor: float = 0.9,
) -> Fidelity:
    """Compare a rendered compression against the payload it came from.

    Args:
        records: The original payload.
        structure: What the extractor recovered.
        plan: What the allocator decided to emit.
        scores: Salience per record index.
        rendered: The compressed output.
        verbatim_floor: Salience at or above which a record is critical.

    Returns:
        A Fidelity report.
    """
    if not records:
        return Fidelity(1.0, 1.0, 1.0, 1.0, 0)

    # How a record is "accounted for" depends on how its extractor conveys it.
    # Template extraction carries members in the pattern line and its count;
    # schema collapse carries them as individual rows. Counting a pattern line
    # as standing in for members that were actually meant to be rows is how a
    # payload can look fully conserved while 98% of it was dropped.
    row_indices = {index for index, _ in plan.rows}
    conveyed_by_rows = {index for index, _ in structure.compact_rows}

    accounted: set[int] = set(plan.verbatim) | row_indices
    for group in plan.groups:
        members = set(group.members)
        if members & conveyed_by_rows:
            continue  # these records travel as rows; only rendered rows count
        accounted |= members

    count_conservation = min(1.0, len(accounted) / len(records))

    present = sum(1 for g in structure.groups if f"[{g.label}]" in rendered)
    pattern_coverage = present / len(structure.groups) if structure.groups else 1.0

    by_index = {r.index: r for r in records}
    critical = [i for i, s in scores.items() if s >= verbatim_floor]

    if not critical:
        return Fidelity(count_conservation, pattern_coverage, 1.0, 1.0, 0)

    kept = sum(1 for i in critical if by_index[i].raw in rendered)
    critical_recall = kept / len(critical)

    wanted: set[str] = set()
    for i in critical:
        wanted |= entities(by_index[i].raw)
    found = wanted & entities(rendered)
    critical_entities = len(found) / len(wanted) if wanted else 1.0

    return Fidelity(
        count_conservation=count_conservation,
        pattern_coverage=pattern_coverage,
        critical_recall=critical_recall,
        critical_entities=critical_entities,
        n_critical=len(critical),
    )
