"""Per-record routing for heterogeneous payloads.

A single shared log pipeline carries several formats at once: application
lines from one service, structured events from another, stack traces from a
third. Picking one whole-payload winner means the loser's records get mangled
— on the mixed corpus, template extraction wins on volume and then shreds the
JSON records into patterns like:

    <*> 07, 2026","discovered_at":"2026-04-07 <*>

which is not compression, just damage. This router asks each extractor to
claim records individually, partitions the payload accordingly, extracts each
partition with the tool that understands it, and merges the results.

It only competes when a payload is genuinely mixed. On a homogeneous payload
it defers, so the simpler extractor wins and nothing is wrapped for nothing.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from packitless.extractors.base import SNIFF_SAMPLE, Extractor, Group, Structure
from packitless.types import Record

logger = logging.getLogger(__name__)

# Below this share of records, a format is treated as noise rather than a
# partition worth extracting separately.
MIN_PARTITION_SHARE = 0.02


class HybridExtractor:
    """Routes each record to the extractor that claims it most strongly."""

    name = "hybrid"

    def __init__(self, members: list[Extractor]) -> None:
        self._members = members
        # A merged result is reconstructable only if every partition is.
        self.reconstructable = all(
            getattr(m, "reconstructable", False) for m in members
        )

    def claim(self, record: Record) -> float:
        """Best claim among members — the router is only as good as its parts."""
        return max((m.claim(record) for m in self._members), default=0.0)

    def _partition(self, records: list[Record]) -> dict[str, list[Record]]:
        """Assign each record to its highest-claiming extractor."""
        buckets: dict[str, list[Record]] = defaultdict(list)
        for record in records:
            best = max(self._members, key=lambda m: m.claim(record))
            buckets[best.name].append(record)
        return buckets

    def sniff(self, records: list[Record]) -> float:
        """Score only heterogeneous payloads; defer on homogeneous ones.

        Returning 0.0 when one extractor claims nearly everything keeps the
        router out of the way — a pure log file should be handled by the line
        extractor directly, not by a router wrapping it.
        """
        sample = records[:SNIFF_SAMPLE]
        if not sample:
            return 0.0

        buckets = self._partition(sample)
        significant = {
            name: bucket
            for name, bucket in buckets.items()
            if len(bucket) / len(sample) >= MIN_PARTITION_SHARE
        }
        if len(significant) < 2:
            return 0.0  # homogeneous — let the specialist win outright

        by_name = {m.name: m for m in self._members}
        total = sum(len(b) for b in significant.values())
        return sum(
            by_name[name].sniff(bucket) * len(bucket) / total
            for name, bucket in significant.items()
        )

    def extract(self, records: list[Record]) -> Structure:
        """Extract each partition with its own extractor, then merge."""
        buckets = self._partition(records)
        by_name = {m.name: m for m in self._members}

        groups: list[Group] = []
        legend: dict = {"schemas": {}, "dictionaries": {}}
        compact_rows: list[tuple[int, str]] = []
        notes: list[str] = []
        reconstructable = True
        weighted_savings = 0.0
        total_bytes = sum(len(r.raw) for r in records) or 1

        for name, bucket in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            structure = by_name[name].extract(bucket)
            bucket_bytes = sum(len(r.raw) for r in bucket)

            relabel = _resolve_label_collisions(structure, groups)
            groups.extend(structure.groups)
            compact_rows.extend(
                (index, _apply_relabel(text, relabel))
                for index, text in structure.compact_rows
            )

            legend["schemas"].update(structure.legend.get("schemas") or {})
            legend["dictionaries"].update(structure.legend.get("dictionaries") or {})

            # The merged payload is only reconstructable if every partition is.
            reconstructable &= structure.reconstructable
            weighted_savings += structure.compression_estimate(len(bucket)) * (
                bucket_bytes / total_bytes
            )
            notes.append(
                f"{name}: {len(bucket):,} records "
                f"({100 * len(bucket) / len(records):.1f}%), "
                f"{len(structure.groups):,} patterns"
            )

        groups.sort(key=lambda g: g.count, reverse=True)

        return Structure(
            extractor=self.name,
            groups=groups,
            legend=legend,
            notes=notes,
            estimated_savings=weighted_savings,
            compact_rows=compact_rows,
            reconstructable=reconstructable,
        )


def _resolve_label_collisions(
    structure: Structure, existing: list[Group]
) -> dict[str, str]:
    """Rename group labels that clash with already-merged ones.

    Labels appear in rendered rows as a prefix, so a rename has to be applied
    to the rows too — see `_apply_relabel`.
    """
    taken = {g.label for g in existing}
    relabel: dict[str, str] = {}
    for group in structure.groups:
        if group.label not in taken:
            taken.add(group.label)
            continue
        candidate = f"{structure.extractor[0].upper()}{group.label}"
        suffix = 2
        while candidate in taken:
            candidate = f"{structure.extractor[0].upper()}{group.label}_{suffix}"
            suffix += 1
        relabel[group.label] = candidate
        taken.add(candidate)
        # Mutate in place: the schema legend is keyed by label too.
        old = group.label
        group.label = candidate
        if old in (structure.legend.get("schemas") or {}):
            structure.legend["schemas"][candidate] = structure.legend["schemas"].pop(old)
    return relabel


def _apply_relabel(row: str, relabel: dict[str, str]) -> str:
    """Rewrite a row's leading label if it was renamed."""
    if not relabel:
        return row
    label, sep, rest = row.partition("|")
    return f"{relabel.get(label, label)}{sep}{rest}"


_: Extractor = HybridExtractor([])  # structural conformance check
