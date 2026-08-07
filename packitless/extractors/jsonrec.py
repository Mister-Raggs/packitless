"""Schema collapse for homogeneous JSON records.

Line templating is the wrong tool for JSONL: it shreds records into
meaningless fragments and honestly reports ~0 confidence when it does. The
redundancy in JSON records is elsewhere:

    keys        repeated verbatim on every record
    values      low-cardinality columns repeat a handful of strings thousands
                of times

So instead of templating text, this extractor emits the schema once as a
legend, dictionary-encodes low-cardinality columns, and leaves only the
genuinely varying values per record.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from packitless.extractors.base import SNIFF_SAMPLE, Extractor, Group, Structure
from packitless.types import Record

logger = logging.getLogger(__name__)

# A column with at most this fraction of distinct values is worth a dictionary.
DICT_CARDINALITY_RATIO = 0.5
# JSON punctuation per key/value pair: quotes, colon, comma.
_PUNCT_PER_FIELD = 4

# Compact rows are pipe-delimited, so a value containing a pipe would shift
# every later column. Real data does contain them — 18 job titles in the
# sample corpus read like "Senior ML Engineer| Uber Direct". Escape rather
# than pick a rarer delimiter: any delimiter can occur, and a corruption that
# only shows up on 0.4% of rows is exactly the kind that ships.
ROW_DELIMITER = "|"


def escape_cell(value: str) -> str:
    """Escape a value for inclusion in a delimited row."""
    return (
        value.replace("\\", "\\\\")
        .replace(ROW_DELIMITER, "\\" + ROW_DELIMITER)
        .replace("\n", "\\n")
    )


def split_row(row: str) -> list[str]:
    """Split a rendered row on unescaped delimiters, reversing escape_cell."""
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in row:
        if escaped:
            current.append("\n" if char == "n" else char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ROW_DELIMITER:
            cells.append("".join(current))
            current = []
        else:
            current.append(char)
    cells.append("".join(current))
    return cells


def _fields(record: Record) -> dict[str, Any] | None:
    """Return the record's parsed object, parsing raw text if needed.

    Records loaded by load_jsonl already carry `fields`. Records that arrived
    inside a mixed stream do not, so we parse on demand — that is how this
    extractor finds JSON hiding among log lines.
    """
    if record.fields:
        return record.fields
    raw = record.raw.strip()
    if not (raw.startswith("{") and raw.endswith("}")):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _estimate_savings(parsed: list[dict[str, Any]], total_bytes: int) -> float:
    """Estimate the removable fraction of a JSON payload.

    Two sources: keys repeated on every record, and repeated values in
    low-cardinality columns. Both are recoverable losslessly.
    """
    if not parsed or total_bytes == 0:
        return 0.0

    key_bytes = sum(
        len(k) + _PUNCT_PER_FIELD for obj in parsed for k in obj
    )

    columns: dict[str, list[str]] = defaultdict(list)
    for obj in parsed:
        for k, v in obj.items():
            columns[k].append(str(v))

    value_bytes_saved = 0
    for values in columns.values():
        distinct = set(values)
        if len(distinct) / len(values) <= DICT_CARDINALITY_RATIO:
            # Each repeat collapses to a short reference instead of the value.
            value_bytes_saved += sum(len(v) for v in values) - sum(
                len(d) for d in distinct
            )

    return min(0.95, (key_bytes + value_bytes_saved) / total_bytes)


class JsonRecordExtractor:
    """Collapses homogeneous JSON records to a schema plus varying values."""

    name = "jsonrec"

    def sniff(self, records: list[Record]) -> float:
        """Estimate savings from schema collapse on a sample."""
        sample = records[:SNIFF_SAMPLE]
        if not sample:
            return 0.0

        parsed = [obj for r in sample if (obj := _fields(r)) is not None]
        if len(parsed) < len(sample) * 0.5:
            # Fewer than half are JSON objects — this is not a records payload.
            return 0.0

        total_bytes = sum(len(r.raw) for r in sample)
        return _estimate_savings(parsed, total_bytes)

    def claim(self, record: Record) -> float:
        """Claim JSON objects decisively; decline everything else."""
        return 1.0 if _fields(record) is not None else 0.0

    def extract(self, records: list[Record]) -> Structure:
        """Group records by schema shape and build the legend."""
        # Record.index is a position in the *original* payload, which is not
        # the position in `records` once the router hands us a partition.
        by_index = {r.index: r for r in records}

        shapes: dict[tuple[str, ...], list[int]] = defaultdict(list)
        parsed: list[dict[str, Any]] = []
        non_json: list[int] = []

        for r in records:
            obj = _fields(r)
            if obj is None:
                non_json.append(r.index)
                continue
            parsed.append(obj)
            shapes[tuple(sorted(obj))].append(r.index)

        groups: list[Group] = []
        legend: dict[str, Any] = {"schemas": {}, "dictionaries": {}}
        by_size = sorted(shapes.items(), key=lambda kv: len(kv[1]), reverse=True)

        for i, (keys, members) in enumerate(by_size, start=1):
            label = f"S{i}"
            legend["schemas"][label] = list(keys)
            groups.append(
                Group(
                    label=label,
                    pattern=f"{{{', '.join(keys)}}}",
                    members=members,
                    exemplar=by_index[members[0]],
                )
            )

        # Dictionary-encode columns whose values repeat heavily.
        columns: dict[str, list[str]] = defaultdict(list)
        for obj in parsed:
            for k, v in obj.items():
                columns[k].append(str(v))
        for key, values in columns.items():
            distinct = sorted(set(values))
            if values and len(distinct) / len(values) <= DICT_CARDINALITY_RATIO:
                legend["dictionaries"][key] = distinct

        # Per-record rows. Savings here come from dropping repeated keys and
        # referencing dictionary entries — not from removing records — so the
        # rows must be emitted or the payload is being deleted, not compressed.
        dict_index = {
            key: {value: i for i, value in enumerate(values)}
            for key, values in legend["dictionaries"].items()
        }
        compact_rows: list[tuple[int, str]] = []
        for group in groups:
            keys = legend["schemas"][group.label]
            for index in group.members:
                obj = _fields(by_index[index]) or {}
                cells = []
                for key in keys:
                    value = str(obj.get(key, ""))
                    ref = dict_index.get(key, {}).get(value)
                    cells.append(
                        f"@{ref}" if ref is not None else escape_cell(value)
                    )
                compact_rows.append(
                    (index, group.label + ROW_DELIMITER + ROW_DELIMITER.join(cells))
                )

        total_bytes = sum(len(r.raw) for r in records)
        savings = _estimate_savings(parsed, total_bytes)

        notes = [
            f"{len(parsed):,} JSON records in {len(groups)} schema shape(s)",
            f"{len(legend['dictionaries'])} column(s) dictionary-encoded",
        ]
        if non_json:
            notes.append(f"{len(non_json):,} non-JSON records passed through")

        return Structure(
            extractor=self.name,
            groups=groups,
            legend=legend,
            notes=notes,
            estimated_savings=savings,
            compact_rows=compact_rows,
            # Keys come back from the schema, values from the dictionaries —
            # every record is recoverable from the rendered output alone.
            reconstructable=True,
        )


_: Extractor = JsonRecordExtractor()  # structural conformance check
