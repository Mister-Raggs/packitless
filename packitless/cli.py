"""Command-line filter: compress a payload on stdin, write it to stdout.

    kubectl logs deploy/api --tail=5000 | packitless --budget 800 --stats
    pytest -v | packitless --stats
    packitless --explain < some.log

This is the integration surface that needs no cooperation from the calling
tool: anything that emits text can be piped through it. Stats go to stderr so
stdout stays clean for the next stage of a pipeline.

Token counting defaults to the free heuristic so the CLI works offline and
without an API key. Pass --exact for real tokenizer counts.
"""

from __future__ import annotations

import argparse
import json
import sys

from packitless.compress import CompressConfig, compress
from packitless.config import load_env
from packitless.corpora import load_jsonl_text, load_lines_text
from packitless.extractors import sniff_all
from packitless.tokens import HeuristicCounter, get_counter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="packitless",
        description="Budget-aware context compression for repetitive payloads.",
    )
    parser.add_argument(
        "--budget", type=int, default=None, metavar="N",
        help="token ceiling for the output; omit for structural compression only",
    )
    parser.add_argument(
        "--extractor", default="auto",
        help="force an extractor (lines, jsonrec, hybrid, passthrough); default auto",
    )
    parser.add_argument(
        "--max-verbatim", type=int, default=10,
        help="cap on records preserved verbatim (default 10)",
    )
    parser.add_argument(
        "--verbatim-floor", type=float, default=0.9,
        help="salience at or above which a record is always kept (default 0.9)",
    )
    parser.add_argument(
        "--exact", action="store_true",
        help="use the real tokenizer instead of the offline heuristic",
    )
    parser.add_argument(
        "--stats", action="store_true", help="write a summary line to stderr"
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="write a JSON report to stdout instead of compressed text",
    )
    parser.add_argument(
        "--explain", action="store_true",
        help="report sniff scores per extractor and exit without compressing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    text = sys.stdin.read()
    if not text.strip():
        print("packitless: empty input", file=sys.stderr)
        return 1

    # JSONL is worth detecting up front so parsed fields ride along; anything
    # else is treated as lines and the sniffer sorts it out.
    first = text.lstrip()[:1]
    records = (
        load_jsonl_text(text) if first == "{" else load_lines_text(text)
    )

    if args.explain:
        for name, score in sniff_all(records):
            print(f"{name:10} {score:.3f}")
        return 0

    if args.exact:
        load_env()
        counter = get_counter()
    else:
        counter = HeuristicCounter()

    config = CompressConfig(
        name="cli",
        budget_tokens=args.budget,
        extractor=args.extractor,
        max_verbatim=args.max_verbatim,
        verbatim_floor=args.verbatim_floor,
    )
    ctx = compress(records, config, counter)

    before = counter.count(text)
    after = counter.count(ctx.text)
    saved = 100.0 * (before - after) / before if before else 0.0

    if args.as_json:
        json.dump(
            {
                "records": ctx.records_in,
                "patterns": ctx.groups,
                "tokens_before": before,
                "tokens_after": after,
                "saved_pct": round(saved, 2),
                "counter": counter.name,
                "dropped": ctx.dropped,
                **ctx.stats,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        sys.stdout.write(ctx.text + "\n")

    if args.stats:
        stats = ctx.stats
        print(
            f"{ctx.records_in:,} records · {ctx.groups:,} patterns · "
            f"{before:,} → {after:,} tok ({saved:.1f}% ↓) · "
            f"{stats.get('extractor')}/{stats.get('confidence', 0):.3f} · "
            f"{stats.get('guarantee')}"
            + (f" · counter={counter.name}" if not args.exact else ""),
            file=sys.stderr,
        )
        for note in ctx.dropped:
            if "dropped for budget" in note or "over the" in note:
                print(f"  warning: {note}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
