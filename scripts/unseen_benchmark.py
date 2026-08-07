"""Measure generalisation on logs the tool was never tuned against.

Every number in the main benchmark comes from corpora used while building the
extractors, which makes them a weak claim about unseen data. This script
points packitless at whatever real logs exist on the host — Apple installer
logs, WiFi driver logs, filesystem checks, application debug logs — and
reports the distribution rather than a single flattering figure.

Usage:
    python scripts/unseen_benchmark.py [--lines N] [--exact] PATH [PATH ...]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from packitless.compress import CompressConfig, compress
from packitless.config import load_env
from packitless.corpora import load_jsonl_text, load_lines_text
from packitless.extractors import sniff_all
from packitless.tokens import HeuristicCounter, get_counter


def measure(path: Path, lines: int, counter) -> dict | None:
    """Compress the tail of one file and report what happened."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"  skip {path.name}: {exc}", file=sys.stderr)
        return None

    tail = "\n".join(text.splitlines()[-lines:])
    if not tail.strip():
        return None

    records = (
        load_jsonl_text(tail) if tail.lstrip()[:1] == "{" else load_lines_text(tail)
    )
    if len(records) < 50:
        return None

    ctx = compress(records, CompressConfig(name="unseen"), counter)
    before, after = counter.count(tail), counter.count(ctx.text)
    best_name, best_score = sniff_all(records)[0]

    return {
        "name": path.name,
        "records": len(records),
        "patterns": ctx.groups,
        "before": before,
        "after": after,
        "saved": 100.0 * (before - after) / before if before else 0.0,
        "extractor": ctx.stats.get("extractor", "?"),
        "confidence": best_score,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--lines", type=int, default=4000,
                        help="tail N lines from each file (default 4000)")
    parser.add_argument("--exact", action="store_true",
                        help="use the real tokenizer instead of the heuristic")
    args = parser.parse_args()

    if args.exact:
        load_env()
        counter = get_counter()
    else:
        counter = HeuristicCounter()

    results = [r for p in args.paths if (r := measure(p, args.lines, counter))]
    if not results:
        print("no usable inputs", file=sys.stderr)
        return 1

    print(f"\n{'log':32} {'records':>8} {'patterns':>9} {'tok in':>9} "
          f"{'tok out':>8} {'saved':>7} {'conf':>6} {'extractor':>9}")
    print("-" * 94)
    for r in sorted(results, key=lambda r: -r["saved"]):
        print(f"{r['name'][:32]:32} {r['records']:8,} {r['patterns']:9,} "
              f"{r['before']:9,} {r['after']:8,} {r['saved']:6.1f}% "
              f"{r['confidence']:6.3f} {r['extractor']:>9}")

    saved = [r["saved"] for r in results]
    print("-" * 94)
    print(f"{'n=' + str(len(results)):32} "
          f"median {statistics.median(saved):.1f}%   "
          f"mean {statistics.mean(saved):.1f}%   "
          f"min {min(saved):.1f}%   max {max(saved):.1f}%")
    print(f"token counter: {counter.name}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
