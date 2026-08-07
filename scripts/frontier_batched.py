"""Cost/quality frontier, batched — a sharper instrument for ~1/5 the spend.

The first version of this experiment scored each summary in its own judge
call, and every one of those calls re-sent the same 18,481-token log payload
as ground truth. Forty-eight calls, 887,100 tokens of identical text, 82% of
the total spend. A token-reduction project measured by a harness that ignored
the most obvious token optimisation available to it.

This version sends the logs **once per incident** alongside every summary of
it, and asks the judge to score them together. Two things improve:

    cost      raw-log tokens drop from 48 payloads to 16 — roughly 5x cheaper
    accuracy  the judge sees the summaries side by side, so it can express
              "this one dropped the disk failure" instead of independently
              guessing an integer for each. Absolute scoring on a 1-5 rubric
              could not resolve the gaps we cared about; comparison can.

The rubric is still Flare's, loaded from its own prompts.py — only the
envelope carrying multiple summaries is ours, and it quotes Flare's scoring
definitions verbatim.

Usage:
    python scripts/frontier_batched.py --flare-repo PATH [--incidents N]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import statistics
import sys
from pathlib import Path

from packitless.compress import CompressConfig, compress
from packitless.config import load_env
from packitless.corpora import load_lines_text
from packitless.tokens import get_counter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from frontier import build_incidents, call_json, load_flare_prompts  # noqa: E402

DEFAULT_MODEL = "claude-sonnet-5"
SUMMARY_MAX_TOKENS = 4096
# The judge compares six candidates at once and reasons hard before writing.
# Thinking is billed against the same ceiling as the output, so this needs
# real headroom or the reply comes back empty.
JUDGE_MAX_TOKENS = 12000
LEVELS: list = ["baseline", 1500, 800, 400, 200, None]

BATCH_JUDGE_SYSTEM = """\
You are an expert evaluator assessing several AI-generated analyses of the SAME \
incident. You will be given the original log data once, followed by {n} candidate \
analyses labelled by id.

Score every candidate on three dimensions, using these definitions:

- relevance (1-5): Does the explanation accurately describe what the logs show?
  1=completely wrong, 3=partially correct, 5=perfectly matches the evidence
- specificity (1-5): Is the explanation specific to THIS incident?
  1=generic boilerplate, 3=somewhat specific, 5=deeply specific to the logs
- actionability (1-5): Are the remediation steps concrete and useful?
  1=vague platitudes, 3=reasonable but generic, 5=specific actionable steps

Because you can see the candidates together, compare them directly. If one omits \
something the logs clearly show and another catches it, that difference must appear \
in the scores. Do not award identical scores out of caution — if two are genuinely \
equivalent, say so by scoring them equally, but look hard for real differences first.

You MUST respond with valid JSON matching this exact schema:
{{
  "candidates": [
    {{
      "id": "<the candidate id>",
      "relevance": <integer 1-5>,
      "specificity": <integer 1-5>,
      "actionability": <integer 1-5>,
      "missed": ["<specific fact present in the logs but absent from this analysis>"],
      "rank": <integer, 1 = best of the set>
    }}
  ]
}}"""


def summarise(client, model, prompts, incident, level, counter) -> tuple[str, dict, int]:
    """Produce one summary of `incident` at one compression level."""
    sum_sys, sum_user, _, _ = prompts
    label = "baseline" if level == "baseline" else f"budget={level}"

    if level == "baseline":
        payload = incident.raw_text
    else:
        payload = compress(
            incident.records,
            CompressConfig(name=label, budget_tokens=level),
            counter,
        ).text

    summary = call_json(
        client, model, sum_sys,
        sum_user.format(
            incident_id=incident.ident, time_range_start="n/a", time_range_end="n/a",
            block_ids="n/a", mean_anomaly_score=0.0, severity=0.0,
            log_line_count=len(incident.records), log_lines=payload,
            templates="(supplied inline)",
        ),
        max_tokens=SUMMARY_MAX_TOKENS,
    )
    return label, summary, counter.count(payload)


def judge_incident(client, model, incident, summaries: dict) -> dict:
    """Score every summary of one incident in a single call."""
    blocks = []
    for label, summary in summaries.items():
        blocks.append(
            f"### Candidate `{label}`\n"
            f"**Explanation:** {summary.get('explanation', '')}\n"
            f"**Severity:** {summary.get('severity', '')}\n"
            f"**Root Cause:** {summary.get('root_cause', '')}\n"
            f"**Remediation Steps:**\n{json.dumps(summary.get('remediation', []))}"
        )

    user = (
        "## Original Log Lines\n"
        f"{incident.raw_text}\n\n"
        "## Candidate Analyses\n\n" + "\n\n".join(blocks) +
        "\n\nScore every candidate. Respond with the JSON object only."
    )
    result = call_json(
        client, model, BATCH_JUDGE_SYSTEM.format(n=len(summaries)), user,
        max_tokens=JUDGE_MAX_TOKENS,
    )
    return {c["id"]: c for c in result.get("candidates", [])}


def run_incident(client, model, prompts, incident, counter) -> list[dict]:
    """Summarise at every level, then judge them together."""
    summaries, tokens = {}, {}
    for level in LEVELS:
        label, summary, tok = summarise(client, model, prompts, incident, level, counter)
        summaries[label] = summary
        tokens[label] = tok

    scored = judge_incident(client, model, incident, summaries)

    rows = []
    for label in summaries:
        s = scored.get(label)
        if not s:
            continue
        rows.append({
            "incident": incident.ident, "level": label,
            "payload_tokens": tokens[label],
            "relevance": float(s["relevance"]),
            "specificity": float(s["specificity"]),
            "actionability": float(s["actionability"]),
            "rank": int(s.get("rank", 0)),
            "missed": s.get("missed", []),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flare-repo", type=Path, required=True)
    parser.add_argument("--log", type=Path, default=ROOT / "corpora" / "hdfs_demo.log")
    parser.add_argument("--incidents", type=int, default=12)
    parser.add_argument("--lines-each", type=int, default=250)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", type=Path,
                        default=ROOT / "results" / "frontier_batched.json")
    args = parser.parse_args()

    load_env()
    import anthropic

    client = anthropic.Anthropic()
    counter = get_counter()
    prompts = load_flare_prompts(args.flare_repo)
    incidents = build_incidents(args.log, args.incidents, args.lines_each)

    calls = len(incidents) * (len(LEVELS) + 1)
    print(f"{len(incidents)} incidents x {len(LEVELS)} levels — {calls} API calls "
          f"({len(incidents)} judge calls, not {len(incidents) * len(LEVELS)})",
          file=sys.stderr)

    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_incident, client, args.model, prompts, inc, counter): inc
            for inc in incidents
        }
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                results.extend(future.result())
            except Exception as exc:  # noqa: BLE001
                print(f"\n  incident {futures[future].ident} failed: "
                      f"{type(exc).__name__}: {exc}", file=sys.stderr)
            print(f"\r  {i}/{len(futures)} incidents", end="", file=sys.stderr)
    print(file=sys.stderr)

    by_level: dict[str, list[dict]] = {}
    for r in results:
        by_level.setdefault(r["level"], []).append(r)

    order = ["baseline"] + [f"budget={l}" for l in LEVELS[1:]]
    baseline = statistics.mean(
        r["payload_tokens"] for r in by_level.get("baseline", [{"payload_tokens": 1}])
    )

    print(f"\n{'level':14} {'tok':>8} {'saved':>7} {'relev':>6} {'spec':>6} "
          f"{'act':>6} {'mean':>6} {'sd':>5} {'rank':>5} {'missed':>7} {'n':>3}")
    print("-" * 86)
    for level in order:
        rows = by_level.get(level)
        if not rows:
            continue
        means = [(r["relevance"] + r["specificity"] + r["actionability"]) / 3 for r in rows]
        tok = statistics.mean(r["payload_tokens"] for r in rows)
        sd = statistics.stdev(means) if len(means) > 1 else 0.0
        print(f"{level:14} {tok:8,.0f} {100 * (baseline - tok) / baseline:6.1f}% "
              f"{statistics.mean(r['relevance'] for r in rows):6.2f} "
              f"{statistics.mean(r['specificity'] for r in rows):6.2f} "
              f"{statistics.mean(r['actionability'] for r in rows):6.2f} "
              f"{statistics.mean(means):6.2f} {sd:5.2f} "
              f"{statistics.mean(r['rank'] for r in rows):5.2f} "
              f"{statistics.mean(len(r['missed']) for r in rows):7.2f} {len(rows):3}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
