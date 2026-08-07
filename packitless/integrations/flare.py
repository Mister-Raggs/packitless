"""Adapter for Flare (github.com/Mister-Raggs/flare), an LLM log-triage service.

Flare builds its summarisation prompt like this:

    log_lines_text = "\\n".join(f"  {line}" for line in incident.log_lines[:50])
    templates_text = "\\n".join(f"  - {tmpl}" for tmpl in incident.templates)

and then interpolates *both* into one prompt — the raw lines and the Drain
templates that summarise those same lines. The templates are a compression of
the log lines, so the prompt carries the data and its own summary side by
side. It also truncates to 50 lines, which is a budget expressed as a row
count rather than a token count: 50 verbose lines and 50 terse ones cost very
different amounts, and neither is the number the context window cares about.

This adapter replaces both with a single budgeted payload. The caller states
a token budget; packitless decides how to spend it across templates, counts,
and the records worth keeping verbatim.

Integration is one call:

    from packitless.integrations.flare import build_payload

    payload = build_payload(incident.log_lines, budget_tokens=800)
    user_prompt = SUMMARIZE_USER_PROMPT.format(
        ..., log_lines=payload, templates="(included above)"
    )
"""

from __future__ import annotations

from packitless.compress import CompressConfig, compress
from packitless.tokens import TokenCounter
from packitless.types import Record

DEFAULT_BUDGET = 800


def build_payload(
    log_lines: list[str],
    budget_tokens: int | None = DEFAULT_BUDGET,
    anomaly_scores: list[float] | None = None,
    counter: TokenCounter | None = None,
) -> str:
    """Build a budgeted log payload for Flare's summarisation prompt.

    Args:
        log_lines: The incident's log lines, unabridged. Pass everything —
            truncating before compression throws away the repetition that
            makes compression work.
        budget_tokens: Token ceiling for the payload. None compresses
            structurally without a cap.
        anomaly_scores: Optional per-line anomaly scores in [0, 1], aligned
            with `log_lines`. Flare already computes these, and a caller's own
            scores always beat inferred salience — supplying them is what
            guarantees the anomalous lines survive verbatim.
        counter: Token counter for budget decisions. Defaults to the best
            available.

    Returns:
        Text ready to interpolate into the prompt.
    """
    records = [
        Record(
            index=i,
            raw=line,
            salience=anomaly_scores[i] if anomaly_scores and i < len(anomaly_scores) else 0.0,
        )
        for i, line in enumerate(log_lines)
        if line.strip()
    ]

    if not records:
        return "(no log lines available)"

    return compress(
        records,
        CompressConfig(name="flare", budget_tokens=budget_tokens),
        counter,
    ).text


def build_payload_verbose(
    log_lines: list[str],
    budget_tokens: int | None = DEFAULT_BUDGET,
    anomaly_scores: list[float] | None = None,
    counter: TokenCounter | None = None,
):
    """Same as `build_payload`, but also returns the CompressedContext.

    Useful when the host wants to log what compression did — which guarantee
    it achieved, how many records were dropped for budget — rather than only
    the text. Flare's own usage tracking should record this alongside its
    token counts.
    """
    records = [
        Record(
            index=i,
            raw=line,
            salience=anomaly_scores[i] if anomaly_scores and i < len(anomaly_scores) else 0.0,
        )
        for i, line in enumerate(log_lines)
        if line.strip()
    ]
    ctx = compress(
        records,
        CompressConfig(name="flare", budget_tokens=budget_tokens),
        counter,
    )
    return ctx.text, ctx
