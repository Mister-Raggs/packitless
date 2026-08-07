"""Collect every measurement into one JSON for the results page.

The page is built from this file, so no number on it is hand-copied. Re-run
after any change and the page regenerates from measured values.

Usage:
    python scripts/export_report.py [--exact]
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from packitless import extractors
from packitless.allocator import allocate
from packitless.compress import CompressConfig, compress
from packitless.config import load_env
from packitless.corpora import discover, load_lines_text
from packitless.extractors.lines import mask
from packitless.fidelity import measure
from packitless.integrations.flare import build_payload_verbose
from packitless.render import render
from packitless.salience import score_records
from packitless.tokens import HeuristicCounter, get_counter

ROOT = Path(__file__).resolve().parent.parent
BUDGETS: list[int | None] = [None, 4000, 1500, 800, 400, 200]

# Payloads used to show where the tool declines. Paths resolved at runtime;
# missing ones are skipped rather than faked.
SCOPE_PROBES = {
    "prose (README)": ROOT / "README.md",
    "source code": ROOT / "packitless" / "compress.py",
}


def corpus_section(corpus, counter) -> dict:
    """Baselines, budget sweep, and fidelity for one corpus."""
    before = counter.count(corpus.text)
    extractor, confidence = extractors.select(corpus.records)
    structure = extractor.extract(corpus.records)
    scores = score_records(corpus.records, structure)

    sweep = []
    for budget in BUDGETS:
        config = CompressConfig(name=f"b{budget}", budget_tokens=budget)
        plan = allocate(
            records=corpus.records, structure=structure, scores=scores,
            counter=counter, budget_tokens=budget,
            verbatim_floor=config.verbatim_floor, max_verbatim=config.max_verbatim,
        )
        rendered = render(corpus.records, structure, plan, scores)
        fid = measure(corpus.records, structure, plan, scores, rendered,
                      config.verbatim_floor)
        after = counter.count(rendered)
        sweep.append({
            "budget": budget,
            "tokens_after": after,
            "saved_pct": round(100 * (before - after) / before, 2),
            "count_conservation": round(fid.count_conservation, 4),
            "pattern_coverage": round(fid.pattern_coverage, 4),
            "critical_recall": round(fid.critical_recall, 4),
            "passed": fid.passed,
        })

    ctx = compress(corpus.records, CompressConfig(name="unbounded"), counter)
    return {
        "name": corpus.name,
        "difficulty": corpus.difficulty,
        "source": corpus.source,
        "records": len(corpus.records),
        "tokens_before": before,
        "extractor": extractor.name,
        "confidence": round(confidence, 4),
        "guarantee": ctx.stats["guarantee"],
        "patterns": [
            {"label": g.label, "count": g.count, "pattern": g.pattern}
            for g in structure.groups[:40]
        ],
        "total_patterns": len(structure.groups),
        "sweep": sweep,
        "sample_input": [r.raw for r in corpus.records[:5]],
        "sample_output": ctx.text.splitlines()[:18],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exact", action="store_true")
    args = parser.parse_args()

    if args.exact:
        load_env()
        counter = get_counter()
    else:
        counter = HeuristicCounter()

    report: dict = {"counter": counter.name, "corpora": [], "scope": []}

    corpora = discover()
    for corpus in corpora:
        report["corpora"].append(corpus_section(corpus, counter))

    # Extractor competition, including payloads it should decline.
    matrix = []
    for corpus in corpora:
        matrix.append({
            "payload": corpus.name,
            "scores": {n: round(s, 4) for n, s in extractors.sniff_all(corpus.records)},
        })
    for label, path in SCOPE_PROBES.items():
        if not path.exists():
            continue
        records = load_lines_text(path.read_text(encoding="utf-8", errors="replace"))
        matrix.append({
            "payload": label,
            "scores": {n: round(s, 4) for n, s in extractors.sniff_all(records)},
        })
    report["extractor_matrix"] = matrix

    # Flare adapter: what the host application spends today vs with the adapter.
    hdfs = next((c for c in corpora if c.name == "hdfs"), None)
    if hdfs:
        lines = [r.raw for r in hdfs.records][:250]
        flare_payload = (
            "\n".join(f"  {l}" for l in lines[:50]) + "\n"
            + "\n".join(f"  - {t}" for t in sorted({mask(l) for l in lines[:50]}))
        )
        flare_tok = counter.count(flare_payload)
        rows = []
        for budget in (800, 400, 200):
            text, ctx = build_payload_verbose(lines, budget_tokens=budget, counter=counter)
            rows.append({
                "budget": budget,
                "tokens": counter.count(text),
                "lines_seen": len(lines),
                "patterns": ctx.groups,
                "guarantee": ctx.stats["guarantee"],
            })
        report["flare"] = {
            "baseline_tokens": flare_tok,
            "baseline_lines_seen": 50,
            "total_lines": len(lines),
            "rows": rows,
        }

    # Previously-measured results that need API credit to reproduce.
    # Prefer the batched run: same incidents, but the judge scored every
    # candidate side by side instead of blind, which resolves differences the
    # absolute-scoring run reported as noise.
    batched = ROOT / "results" / "frontier_batched.json"
    frontier = batched if batched.exists() else ROOT / "results" / "frontier.json"
    report["frontier_method"] = "comparative" if batched.exists() else "absolute"
    if frontier.exists():
        runs = [r for r in json.loads(frontier.read_text()) if not r.get("error")]
        by_level: dict[str, list] = {}
        for r in runs:
            by_level.setdefault(r["level"], []).append(r)
        report["frontier"] = [
            {
                "level": level,
                "payload_tokens": round(statistics.mean(r["payload_tokens"] for r in rs)),
                "relevance": round(statistics.mean(r["relevance"] for r in rs), 3),
                "specificity": round(statistics.mean(r["specificity"] for r in rs), 3),
                "actionability": round(statistics.mean(r["actionability"] for r in rs), 3),
                "rank": round(statistics.mean(r.get("rank", 0) for r in rs), 2),
                "sd": round(statistics.stdev(
                    [(r["relevance"] + r["specificity"] + r["actionability"]) / 3
                     for r in rs]) if len(rs) > 1 else 0.0, 3),
                "n": len(rs),
            }
            for level, rs in by_level.items()
        ]

    out = ROOT / "results" / "report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
    for c in report["corpora"]:
        print(f"  {c['name']:6} {c['tokens_before']:9,} tok  {c['extractor']:8} "
              f"{c['total_patterns']:4,} patterns  {c['guarantee']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
