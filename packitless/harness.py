"""Measurement harness.

Runs a matrix of (corpus, config) pairs and reports measured token counts.
This is the spine of the project: every claim downstream is a row in this
table rather than an assertion.
"""

from __future__ import annotations

import logging

from packitless.compress import CompressConfig, compress
from packitless.corpora import Corpus
from packitless.tokens import TokenCounter
from packitless.types import RunResult

logger = logging.getLogger(__name__)


def run_one(corpus: Corpus, config: CompressConfig, counter: TokenCounter) -> RunResult:
    """Measure one (corpus, config) pair."""
    before = counter.count(corpus.text)
    result = compress(corpus.records, config)
    after = counter.count(result.text)

    return RunResult(
        corpus=corpus.name,
        difficulty=corpus.difficulty,
        config=config.name,
        records_in=result.records_in,
        tokens_before=before,
        tokens_after=after,
        counter=counter.name,
        groups=result.groups,
        records_verbatim=result.records_verbatim,
    )


def run_matrix(
    corpora: list[Corpus],
    configs: list[CompressConfig],
    counter: TokenCounter,
) -> list[RunResult]:
    """Measure every corpus against every config."""
    results: list[RunResult] = []
    for corpus in corpora:
        for config in configs:
            try:
                results.append(run_one(corpus, config, counter))
            except NotImplementedError as exc:
                logger.warning("skipping %s/%s: %s", corpus.name, config.name, exc)
    return results


_HEADERS = ("corpus", "tier", "config", "records", "tok in", "tok out", "saved", "ratio")


def format_table(results: list[RunResult]) -> str:
    """Render results as a fixed-width table."""
    if not results:
        return "(no results)"

    rows = [
        (
            r.corpus,
            r.difficulty,
            r.config,
            f"{r.records_in:,}",
            f"{r.tokens_before:,}",
            f"{r.tokens_after:,}",
            f"{r.saved_pct:.1f}%",
            f"{r.ratio:.2f}x",
        )
        for r in results
    ]

    widths = [
        max(len(_HEADERS[i]), max(len(row[i]) for row in rows))
        for i in range(len(_HEADERS))
    ]

    def line(cells: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells)).rstrip()

    out = [line(_HEADERS), "  ".join("-" * w for w in widths)]
    out.extend(line(row) for row in rows)
    out.append("")
    out.append(f"token counter: {results[0].counter}")
    return "\n".join(out)
