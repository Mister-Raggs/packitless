"""Salience scoring — deciding which records must survive compression.

Compression is only safe if the records that mattered come through intact.
A summary that drops the one stack trace in ten thousand heartbeat lines has
technically compressed and practically destroyed the payload.

Three signals, combined:

    rarity      a record in a 3-member group is more informative than one in
                a 1,146-member group. This is the strongest signal and it is
                free — the extractor already computed group sizes.
    severity    error-shaped vocabulary, weighted by how severe.
    caller      an application that already knows what matters (an anomaly
                score, a failing test) supplies it on the Record and it wins.
"""

from __future__ import annotations

import math
import re

from packitless.extractors.base import Structure
from packitless.types import Record

# Weighted so a FATAL in a large group still outranks a routine line in a
# small one, but rarity dominates when severity is absent.
_SEVERITY: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"\b(FATAL|PANIC|CRITICAL)\b", re.I), 1.00),
    (re.compile(r"\b(ERROR|Exception|Traceback|SEVERE)\b", re.I), 0.85),
    (re.compile(r"\b(fail(ed|ure)?|refused|timeout|timed out|denied)\b", re.I), 0.70),
    (re.compile(r"\b(WARN(ING)?|retry|retrying|degraded)\b", re.I), 0.45),
]

RARITY_WEIGHT = 0.6
SEVERITY_WEIGHT = 0.4


def severity_score(text: str) -> float:
    """Highest-matching severity weight for a line, or 0.0."""
    for pattern, weight in _SEVERITY:
        if pattern.search(text):
            return weight
    return 0.0


def rarity_score(group_size: int, largest_group: int) -> float:
    """How unusual a record is, given the size of the group it belongs to.

    Log-scaled: the interesting gap is between 3 and 30 members, not between
    1,000 and 10,000, so a linear ratio would flatten everything worth seeing.
    """
    if group_size <= 0 or largest_group <= 1:
        return 0.0
    return 1.0 - (math.log1p(group_size) / math.log1p(largest_group))


def score_records(
    records: list[Record],
    structure: Structure,
) -> dict[int, float]:
    """Score every record in [0, 1].

    Args:
        records: The payload.
        structure: Extractor output, used for group sizes.

    Returns:
        Mapping of record index to salience.
    """
    group_size: dict[int, int] = {}
    for group in structure.groups:
        for index in group.members:
            group_size[index] = group.count

    largest = max(group_size.values(), default=1)
    by_index = {r.index: r for r in records}

    scores: dict[int, float] = {}
    for index, record in by_index.items():
        if record.salience > 0.0:
            # The caller knows more than we do.
            scores[index] = min(1.0, record.salience)
            continue
        rarity = rarity_score(group_size.get(index, 1), largest)
        severity = severity_score(record.raw)
        scores[index] = min(
            1.0, RARITY_WEIGHT * rarity + SEVERITY_WEIGHT * severity
        )
    return scores
