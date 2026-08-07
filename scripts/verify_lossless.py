"""Prove the lossless guarantee by round-tripping, not by asserting it.

Parses a rendered compression back into records using only the text of the
output — the schema line, the column dictionaries, and the compact rows — then
compares field-by-field against the originals.

If an extractor claims `reconstructable=True`, this must pass. A guarantee
nobody tests is a guarantee nobody should believe.

Usage:
    python scripts/verify_lossless.py
"""

from __future__ import annotations

import json
import re
import sys

# "  @12 some value" — captured exactly, so values keeping leading or trailing
# spaces survive the round trip.
_DICT_ENTRY = re.compile(r"^  @(\d+) (.*)$")

from packitless.compress import CompressConfig, compress
from packitless.config import load_env
from packitless.corpora import discover
from packitless.extractors.jsonrec import split_row
from packitless.tokens import get_counter


def parse_rendered(text: str) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Recover the schema, dictionaries, and rows from rendered output."""
    fields: list[str] = []
    dictionaries: dict[str, list[str]] = {}
    rows: list[str] = []

    current_dict: str | None = None
    for line in text.splitlines():
        if line.startswith("S1 fields: "):
            fields = [f.strip() for f in line[len("S1 fields: ") :].split(",")]
            current_dict = None
        elif " dictionary (" in line and line.endswith("entries):"):
            current_dict = line.split(" dictionary (")[0]
            dictionaries[current_dict] = []
        elif current_dict and (m := _DICT_ENTRY.match(line)):
            index, value = int(m.group(1)), m.group(2)
            entries = dictionaries[current_dict]
            if index != len(entries):
                raise ValueError(
                    f"{current_dict} dictionary out of order at @{index}"
                )
            entries.append(value)
        elif line.strip().startswith("S1|"):
            rows.append(line.strip())
            current_dict = None
        elif line.strip() == "":
            current_dict = None
    return dictionaries, fields, rows


def reconstruct(dictionaries, fields, rows) -> list[dict[str, str]]:
    """Rebuild the original records from the rendered output alone."""
    out: list[dict[str, str]] = []
    for row in rows:
        cells = split_row(row)[1:]  # drop the schema label
        record: dict[str, str] = {}
        for key, cell in zip(fields, cells):
            if cell.startswith("@") and cell[1:].isdigit():
                record[key] = dictionaries.get(key, [])[int(cell[1:])]
            else:
                record[key] = cell
        out.append(record)
    return out


def main() -> int:
    load_env()
    counter = get_counter()
    corpora = {c.name: c for c in discover()}
    corpus = corpora["jobs"]

    ctx = compress(corpus.records, CompressConfig(name="unbounded"), counter)
    claim = ctx.stats["guarantee"]
    print(f"claimed guarantee: {claim}")

    dictionaries, fields, rows = parse_rendered(ctx.text)
    print(f"parsed back: {len(fields)} fields, "
          f"{len(dictionaries)} dictionaries, {len(rows):,} rows")

    rebuilt = reconstruct(dictionaries, fields, rows)
    originals = [
        {k: str(v) for k, v in (r.fields or json.loads(r.raw)).items()}
        for r in corpus.records
    ]

    if len(rebuilt) != len(originals):
        print(f"FAIL: rebuilt {len(rebuilt):,} records, expected {len(originals):,}")
        return 1

    mismatches = [
        (i, k, originals[i].get(k), rebuilt[i].get(k))
        for i in range(len(originals))
        for k in originals[i]
        if originals[i].get(k) != rebuilt[i].get(k)
    ]

    if mismatches:
        print(f"FAIL: {len(mismatches):,} field mismatches across "
              f"{len({m[0] for m in mismatches}):,} records")
        for i, k, want, got in mismatches[:5]:
            print(f"  record {i} field {k!r}\n    want {want!r}\n    got  {got!r}")
        return 1

    total_fields = sum(len(o) for o in originals)
    print(f"PASS: {len(rebuilt):,} records, {total_fields:,} fields "
          f"reconstructed exactly from rendered output alone")
    print(f"      compression {counter.count(corpus.text):,} -> "
          f"{counter.count(ctx.text):,} tok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
