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
from packitless.corpora import discover, load_jsonl_text, load_lines_text
from packitless.extractors.lines import mask
from packitless.fidelity import measure
from packitless.integrations.flare import build_payload_verbose
from packitless.pricing import RATES, project
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

# One real payload per usage scenario. Every figure on the scenarios section of
# the page is measured from these files at build time — none is illustrative.
SCENARIOS = [
    {
        "key": "agent",
        "title": "An agent reading tool output",
        "blurb": "A coding agent runs a build, a test suite, or kubectl logs "
                 "and pipes thousands of near-identical lines into its context "
                 "window. This is a real installer log — the shape of output an "
                 "agent drowns in every day.",
        "surface": "MCP tool — the model decides to call it",
        "call": "compress_payload(text, budget_tokens=400)",
        "path": Path("/var/log/install.log"),
        "budget": 400,
        "tail": 3000,
    },
    {
        "key": "triage",
        "title": "Incident triage on a live service",
        "blurb": "Flare summarises log anomalies with an LLM. It truncated each "
                 "incident to 50 lines to control cost — a budget in the wrong "
                 "unit. The adapter reads every line for a fraction of the spend.",
        "surface": "Python library — one call in the prompt builder",
        "call": "build_payload(incident.log_lines, budget_tokens=800)",
        "path": ROOT / "corpora" / "hdfs_demo.log",
        "budget": 800,
    },
    {
        "key": "records",
        "title": "Batch processing structured records",
        "blurb": "Classifying or extracting over database rows and API responses, "
                 "where every record repeats the same keys. Schema collapse is "
                 "reversible, so nothing is given up.",
        "surface": "Python library, with require_lossless",
        "call": "compress(records, CompressConfig(require_lossless=True))",
        "path": ROOT / "corpora" / "jobs.jsonl",
        "budget": None,
        "require_lossless": True,
    },
    {
        "key": "pipeline",
        "title": "A shared multi-service log pipeline",
        "blurb": "One stream carrying several formats at once. Each record is "
                 "routed to the extractor that understands it, so structured "
                 "events are not shredded by a line templater.",
        "surface": "CLI — works with any tool, in any language",
        "call": "journalctl -u api | packitless --budget 800 --stats",
        "path": ROOT / "corpora" / "mixed.log",
        "budget": 800,
    },
    {
        "key": "unknown",
        "title": "A log format nobody has seen",
        "blurb": "No tuning, no configuration, no schema. This is a macOS "
                 "WiFi driver log the tool has never been shown.",
        "surface": "CLI",
        "call": "packitless --budget 600 --stats < /var/log/install.log",
        "path": Path("/var/log/wifi.log"),
        "budget": 600,
        "tail": 4000,
    },
    {
        "key": "decline",
        "title": "Something it should refuse",
        "blurb": "Prose has no exploitable repetition. Every extractor scores "
                 "near zero and the payload passes through byte-for-byte — "
                 "knowing when not to act is part of being safe to adopt.",
        "surface": "Any — the answer is the same everywhere",
        "call": "packitless --explain < README.md",
        "path": ROOT / "README.md",
        "budget": None,
    },
]


def scenario_section(spec, counter) -> dict | None:
    """Measure one usage scenario against a real payload."""
    path = spec["path"]
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    if spec.get("tail"):
        text = "\n".join(text.splitlines()[-spec["tail"]:])
    if not text.strip():
        return None

    loader = load_jsonl_text if text.lstrip()[:1] == "{" else load_lines_text
    records = loader(text)
    if len(records) < 20:
        return None

    ctx = compress(
        records,
        CompressConfig(
            name=spec["key"], budget_tokens=spec.get("budget"),
            require_lossless=spec.get("require_lossless", False),
        ),
        counter,
    )
    before, after = counter.count(text), counter.count(ctx.text)
    return {
        "key": spec["key"], "title": spec["title"], "blurb": spec["blurb"],
        "surface": spec["surface"], "call": spec["call"],
        "source": path.name,
        "records": len(records),
        "tokens_before": before,
        "tokens_after": after,
        "saved_pct": round(100 * (before - after) / before, 1) if before else 0.0,
        "extractor": ctx.stats.get("extractor"),
        "guarantee": ctx.stats.get("guarantee"),
        "truncated": ctx.stats.get("truncated", False),
        "patterns": ctx.groups,
        "sections": ctx.stats.get("sections", {}),
        "cost": _cost_block(before, after),
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


def _cost_block(before: int, after: int, calls: int = 1_000) -> dict:
    """Cost of one payload at a stated rate, projected over `calls` calls."""
    s = project(before, after, calls=calls)
    return {
        "model": s.model, "input_rate_per_mtok": s.input_rate, "calls": calls,
        "usd_before": round(s.cost_before, 2),
        "usd_after": round(s.cost_after, 2),
        "usd_saved": round(s.saved, 2),
        "saved_pct": round(s.saved_pct, 1),
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

    report["scenarios"] = [
        row for spec in SCENARIOS
        if (row := scenario_section(spec, counter)) is not None
    ]

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

    report["rates"] = {m: {"input": i, "output": o} for m, (i, o) in RATES.items()}
    if hasattr(counter, "save"):
        report["token_cache_size"] = counter.save()

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
