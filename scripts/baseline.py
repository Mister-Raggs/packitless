"""Measure the uncompressed baseline for every corpus.

This is the number every later claim is measured against. Run it before
building any extractor — if the baselines look wrong, nothing downstream
will be trustworthy.

Usage:
    python scripts/baseline.py [--limit N] [--heuristic]
"""

from __future__ import annotations

import argparse
import logging

from packitless.compress import PASSTHROUGH
from packitless.corpora import discover
from packitless.harness import format_table, run_matrix
from packitless.tokens import get_counter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None, help="cap records per corpus"
    )
    parser.add_argument(
        "--heuristic",
        action="store_true",
        help="force the free approximate counter even if an API key is set",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    corpora = discover(limit=args.limit)
    if not corpora:
        raise SystemExit(
            "no corpora found — run scripts/build_corpora.py first"
        )

    counter = get_counter(prefer_api=not args.heuristic)
    results = run_matrix(corpora, [PASSTHROUGH], counter)

    print()
    print(format_table(results))
    print()
    for corpus in corpora:
        print(f"  {corpus.name:8} [{corpus.difficulty:6}] {corpus.source}")
    print()


if __name__ == "__main__":
    main()
