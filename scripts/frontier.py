"""Measure the cost/quality frontier using Flare's own judge.

The lossless tier proves itself by round-tripping. The lossy tier cannot — so
it has to be measured. This script asks the question directly:

    if we compress the logs before summarising them, does the summary get
    worse, and by how much?

Method, per incident:

    1. Summarise the *uncompressed* logs      -> baseline summary
    2. Summarise the compressed logs at each budget
    3. Score every summary against the ORIGINAL logs

Step 3 is the part that makes this honest. The judge always sees the full
uncompressed payload as ground truth, so a summary that dropped something
real has nowhere to hide — if compression destroyed information, the judge is
looking at the evidence the summary failed to mention.

Prompts are imported from a real Flare checkout rather than copied, so the
claim "scored by Flare's own rubric" is literally true.

Usage:
    python scripts/frontier.py --flare-repo PATH [--incidents N] [--workers N]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

from packitless.compress import CompressConfig, compress
from packitless.config import load_env
from packitless.corpora import load_lines_text
from packitless.tokens import get_counter

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 2048

# Budgets to sweep. None is the structural-only setting (no truncation).
DEFAULT_BUDGETS: list[int | None] = [None, 1500, 800, 400, 200]


@dataclass
class Result:
    incident: int
    level: str
    payload_tokens: int
    relevance: float | None = None
    specificity: float | None = None
    actionability: float | None = None
    error: str | None = None

    @property
    def mean_score(self) -> float | None:
        scores = [self.relevance, self.specificity, self.actionability]
        return statistics.mean(scores) if all(s is not None for s in scores) else None


@dataclass
class Incident:
    ident: int
    records: list = field(default_factory=list)

    @property
    def raw_text(self) -> str:
        return "\n".join(r.raw for r in self.records)


def load_flare_prompts(repo: Path):
    """Load Flare's prompt constants straight from the file.

    Imported by path rather than as `flare.llm.prompts`, because Flare's
    package __init__ pulls in its API client and third-party dependencies we
    have no reason to install. prompts.py is pure constants, so loading it in
    isolation is both safe and faithful — these are Flare's real prompts, not
    a copy that can drift.
    """
    import importlib.util

    path = repo / "flare" / "llm" / "prompts.py"
    if not path.exists():
        sys.exit(f"error: no Flare prompts at {path}")

    spec = importlib.util.spec_from_file_location("flare_prompts", path)
    if spec is None or spec.loader is None:
        sys.exit(f"error: could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return (
        module.SUMMARIZE_SYSTEM_PROMPT,
        module.SUMMARIZE_USER_PROMPT,
        module.QUALITY_EVAL_SYSTEM_PROMPT,
        module.QUALITY_EVAL_USER_PROMPT,
    )


def build_incidents(path: Path, count: int, lines_each: int) -> list[Incident]:
    """Carve contiguous windows out of a log file.

    Contiguous rather than anomaly-clustered: reproducible, and it keeps the
    experiment independent of Flare's detector so a detector change cannot
    silently move the frontier.
    """
    records = load_lines_text(path.read_text(encoding="utf-8", errors="replace"))
    incidents: list[Incident] = []
    for i in range(count):
        start = i * lines_each
        window = records[start : start + lines_each]
        if len(window) < lines_each // 2:
            break
        incidents.append(Incident(ident=i, records=window))
    return incidents


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def call_json(client, model: str, system: str, user: str) -> dict:
    """Call the model and parse a JSON object out of the reply.

    Note: no `temperature` is passed. Flare's client sends temperature=0.0,
    which current models reject with a 400 — another reason its calls would
    fail today.
    """
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    match = _JSON_BLOCK.search(text)
    if not match:
        raise ValueError(f"no JSON in response: {text[:160]}")
    # strict=False: models occasionally emit a raw control character inside a
    # string (a literal newline in an explanation, say). That is invalid JSON
    # by the letter of the spec but perfectly readable, and rejecting it would
    # discard an otherwise good run and bias the sample.
    return json.loads(match.group(0), strict=False)


def run_one(client, model, prompts, incident: Incident, level, counter) -> Result:
    """Summarise one incident at one compression level, then score it."""
    sum_sys, sum_user, eval_sys, eval_user = prompts
    name = "baseline" if level == "baseline" else f"budget={level}"

    if level == "baseline":
        payload = incident.raw_text
    else:
        ctx = compress(
            incident.records,
            CompressConfig(name=name, budget_tokens=level),
            counter,
        )
        payload = ctx.text

    result = Result(
        incident=incident.ident, level=name, payload_tokens=counter.count(payload)
    )

    try:
        summary = call_json(
            client,
            model,
            sum_sys,
            sum_user.format(
                incident_id=incident.ident,
                time_range_start="n/a",
                time_range_end="n/a",
                block_ids="n/a",
                mean_anomaly_score=0.0,
                severity=0.0,
                log_line_count=len(incident.records),
                log_lines=payload,
                templates="(supplied inline)",
            ),
        )
        # The judge always sees the ORIGINAL logs, never the compressed ones.
        scores = call_json(
            client,
            model,
            eval_sys,
            eval_user.format(
                log_lines=incident.raw_text,
                explanation=summary.get("explanation", ""),
                severity=summary.get("severity", ""),
                root_cause=summary.get("root_cause", ""),
                remediation_steps=json.dumps(summary.get("remediation", [])),
            ),
        )
        result.relevance = float(scores["relevance"])
        result.specificity = float(scores["specificity"])
        result.actionability = float(scores["actionability"])
    except Exception as exc:  # noqa: BLE001 — one bad call must not kill the sweep
        result.error = f"{type(exc).__name__}: {exc}"

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flare-repo", type=Path, required=True)
    parser.add_argument("--log", type=Path, default=ROOT / "corpora" / "hdfs_demo.log")
    parser.add_argument("--incidents", type=int, default=6)
    parser.add_argument("--lines-each", type=int, default=250)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "frontier.json")
    args = parser.parse_args()

    load_env()
    import anthropic

    client = anthropic.Anthropic()
    counter = get_counter()
    prompts = load_flare_prompts(args.flare_repo)
    incidents = build_incidents(args.log, args.incidents, args.lines_each)
    levels: list = ["baseline", *DEFAULT_BUDGETS[1:], None]

    jobs = [(inc, lvl) for inc in incidents for lvl in levels]
    print(f"{len(incidents)} incidents x {len(levels)} levels = {len(jobs)} runs "
          f"({len(jobs) * 2} API calls) on {args.model}", file=sys.stderr)

    results: list[Result] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(run_one, client, args.model, prompts, inc, lvl, counter)
            for inc, lvl in jobs
        ]
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            results.append(future.result())
            print(f"\r  {i}/{len(futures)}", end="", file=sys.stderr)
    print(file=sys.stderr)

    by_level: dict[str, list[Result]] = {}
    for r in results:
        by_level.setdefault(r.level, []).append(r)

    order = ["baseline"] + [f"budget={b}" for b in DEFAULT_BUDGETS[1:]] + ["budget=None"]
    baseline_tokens = statistics.mean(
        r.payload_tokens for r in by_level.get("baseline", []) or [Result(0, "", 1)]
    )

    print(f"\n{'level':14} {'payload tok':>12} {'saved':>7} {'relevance':>10} "
          f"{'specific':>9} {'action':>7} {'mean':>6} {'n':>3}")
    print("-" * 78)
    for level in order:
        rows = [r for r in by_level.get(level, []) if r.error is None]
        if not rows:
            continue
        tok = statistics.mean(r.payload_tokens for r in rows)
        print(
            f"{level:14} {tok:12,.0f} {100 * (baseline_tokens - tok) / baseline_tokens:6.1f}% "
            f"{statistics.mean(r.relevance for r in rows):10.2f} "
            f"{statistics.mean(r.specificity for r in rows):9.2f} "
            f"{statistics.mean(r.actionability for r in rows):7.2f} "
            f"{statistics.mean(r.mean_score for r in rows):6.2f} {len(rows):3}"
        )

    errors = [r for r in results if r.error]
    if errors:
        print(f"\n{len(errors)} failed run(s); first: {errors[0].error}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps([r.__dict__ for r in results], indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
