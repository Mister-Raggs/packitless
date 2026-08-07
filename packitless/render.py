"""Rendering.

The output is meant to be read by a model *and* by a human reviewing what the
compressor did. Anything unreadable is unauditable, so this deliberately
avoids token-level pruning tricks that produce garbled text — a reviewer must
be able to look at the output and confirm the incident survived.

One rule governs everything here: **never truncate data for display.** A
column dictionary is the decode table for every row that references it, and a
compact row is the record itself. Shortening either produces output that looks
tidy and cannot be reconstructed — which silently turns a lossless guarantee
into a false one. Only genuinely redundant preview text may be elided.
"""

from __future__ import annotations

from packitless.allocator import Plan
from packitless.extractors.base import Structure
from packitless.types import Record


def render(
    records: list[Record],
    structure: Structure,
    plan: Plan,
    scores: dict[int, float],
) -> str:
    """Render a compressed payload as text for a prompt."""
    by_index = {r.index: r for r in records}
    lines: list[str] = []

    lines.append(
        f"── {len(records):,} records · {len(structure.groups):,} patterns · "
        f"extractor={structure.extractor} ──"
    )

    # Legend: emitted once, replacing per-record repetition. Complete by
    # construction — every dictionary entry a row can reference appears here.
    schemas = structure.legend.get("schemas") or {}
    for label, keys in schemas.items():
        lines.append(f"{label} fields: {', '.join(keys)}")

    dictionaries = structure.legend.get("dictionaries") or {}
    for key, values in dictionaries.items():
        lines.append(f"{key} dictionary ({len(values):,} entries):")
        for i, value in enumerate(values):
            lines.append(f"  @{i} {value}")

    if schemas or dictionaries:
        lines.append("")

    # The structural spine. Patterns are the compressed content for template
    # extraction, so they are emitted in full too.
    for group in plan.groups:
        lines.append(f"[{group.label}] ×{group.count:<7,} {group.pattern}")

    if plan.groups_omitted:
        lines.append(f"(+{plan.groups_omitted:,} smaller patterns omitted for budget)")

    # Compact per-record rows. "@n" refers to entry n of the column dictionary.
    if plan.rows:
        lines.append("")
        for _, text in plan.rows:
            lines.append(f"  {text}")

    if plan.rows_omitted:
        lines.append(
            f"  … {plan.rows_omitted:,} further records dropped for budget "
            f"(payload truncated)"
        )

    # Records preserved in full.
    if plan.verbatim:
        lines.append("")
        lines.append("verbatim (highest salience):")
        for index in plan.verbatim:
            record = by_index.get(index)
            if record is None:
                continue
            lines.append(f"  [{scores.get(index, 0.0):.2f}] {record.raw}")

    if plan.overrun:
        lines.append("")
        lines.append("! budget exceeded to preserve must-keep records")

    return "\n".join(lines)
