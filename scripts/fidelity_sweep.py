"""Sweep budgets and report deterministic fidelity — zero API calls.

This is the free frontier. It answers "how much did we save, and did anything
important go missing" for every corpus at every budget, without spending a
cent, so it can run on every commit instead of once when someone is feeling
generous.

Usage:
    python scripts/fidelity_sweep.py [--exact]
"""

from __future__ import annotations

import argparse
import sys

from packitless import extractors
from packitless.allocator import allocate
from packitless.compress import CompressConfig, compress
from packitless.config import load_env
from packitless.corpora import discover
from packitless.fidelity import measure
from packitless.render import render
from packitless.salience import score_records
from packitless.tokens import HeuristicCounter, get_counter

BUDGETS: list[int | None] = [None, 4000, 1500, 800, 400, 200]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exact", action="store_true",
                        help="use the real tokenizer (needs API credit; falls back to heuristic)")
    args = parser.parse_args()

    if args.exact:
        load_env()
        counter = get_counter()
    else:
        counter = HeuristicCounter()

    failures = 0
    for corpus in discover():
        before = counter.count(corpus.text)
        extractor, confidence = extractors.select(corpus.records)
        structure = extractor.extract(corpus.records)
        scores = score_records(corpus.records, structure)

        print(f"\n{corpus.name} [{corpus.difficulty}] — {extractor.name} "
              f"(conf {confidence:.3f}), {before:,} tok, "
              f"{len(structure.groups):,} patterns")
        print(f"  {'budget':>8} {'tok out':>9} {'saved':>7} {'counts':>7} "
              f"{'patterns':>9} {'critical':>9} {'entities':>9}  verdict")
        print("  " + "-" * 76)

        for budget in BUDGETS:
            config = CompressConfig(name=f"b{budget}", budget_tokens=budget)
            # One pass: allocate and render directly rather than calling
            # compress(), which would repeat the whole pipeline internally.
            plan = allocate(
                records=corpus.records, structure=structure, scores=scores,
                counter=counter, budget_tokens=budget,
                verbatim_floor=config.verbatim_floor,
                max_verbatim=config.max_verbatim,
            )
            rendered = render(corpus.records, structure, plan, scores)
            fid = measure(corpus.records, structure, plan, scores, rendered,
                          config.verbatim_floor)

            after = counter.count(rendered)
            verdict = "ok" if fid.passed else "LOSS"
            if not fid.passed and budget is None:
                # Losing information with no budget pressure is a bug, not a
                # tradeoff — flag it separately from deliberate truncation.
                verdict = "BUG"
                failures += 1

            print(f"  {str(budget):>8} {after:9,} "
                  f"{100 * (before - after) / before:6.1f}% "
                  f"{fid.count_conservation:7.3f} {fid.pattern_coverage:9.3f} "
                  f"{fid.critical_recall:9.3f} {fid.critical_entities:9.3f}"
                  f"  {verdict}")

    print(f"\ntoken counter: {counter.name}")
    if failures:
        print(f"{failures} unbudgeted run(s) lost information — investigate")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
