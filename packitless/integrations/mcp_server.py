"""MCP server — compression as a tool an agent can decide to call.

The third integration shape. A library call is something your code does; a
stdin filter is something your pipeline does; this is something the *model*
does, when it notices it is about to read eight thousand lines of `kubectl
logs` into a context window.

    packitless-mcp                 # stdio, for a local MCP client

Two tools, deliberately:

    analyse   cheap, read-only. "How compressible is this, and by what
              mechanism?" An agent can ask before committing to anything.
    compress  the real thing, under a token budget.

`analyse` exists because the useful answer is sometimes "don't bother" — on
prose or source code every extractor scores near zero, and an agent that can
see that will skip the round trip instead of mangling the payload.
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from packitless.compress import CompressConfig, compress
from packitless.corpora import load_jsonl_text, load_lines_text
from packitless.extractors import sniff_all
from packitless.tokens import HeuristicCounter

# Offline by default: an MCP server should not need the caller's API key, and
# compression itself never makes a model call. The heuristic ranks options
# correctly even though it under-counts dense identifier text.
COUNTER = HeuristicCounter()

# Guard rails. An agent can paste an entire log file; refusing politely beats
# spending a minute of wall clock inside a tool call.
MAX_LINES = 20_000
MAX_CHARS = 4_000_000

server = MCPServer(
    name="packitless",
    title="packitless — context compression",
    instructions=(
        "Compress repetitive machine-generated text before reading it into "
        "context: logs, traces, events, JSON records, test and CI output. "
        "Call `analyse` first when unsure — it is free and tells you whether "
        "compression will help. Prose, source code and commit messages are "
        "not compressible and will pass through unchanged."
    ),
)


def _load(text: str):
    """Parse text into records, detecting JSONL by its first character."""
    if len(text) > MAX_CHARS:
        raise ValueError(
            f"payload is {len(text):,} characters; the limit is {MAX_CHARS:,}. "
            f"Compress it in chunks, or pass a tail."
        )
    loader = load_jsonl_text if text.lstrip()[:1] == "{" else load_lines_text
    records = loader(text, limit=MAX_LINES)
    if not records:
        raise ValueError("payload is empty")
    return records


@server.tool()
def analyse(text: str) -> str:
    """Estimate how compressible a payload is, without compressing it.

    Read-only and free. Returns each extractor's confidence — a compressibility
    estimate, not a format guess — plus the mechanism that would apply. A best
    score below 0.05 means the payload has no exploitable structure and should
    be left alone.

    Args:
        text: The payload to inspect.
    """
    records = _load(text)
    scores = sniff_all(records)
    best, confidence = scores[0]

    mechanism = {
        "lines": "template extraction (repeated message shapes)",
        "jsonrec": "schema collapse (repeated keys and low-cardinality values)",
        "hybrid": "per-record routing (payload carries several formats)",
    }.get(best, best)

    return json.dumps({
        "records": len(records),
        "estimated_tokens": COUNTER.count(text),
        "confidence": {name: round(score, 4) for name, score in scores},
        "best_extractor": best,
        "mechanism": mechanism,
        "worth_compressing": confidence >= 0.05,
        "advice": (
            f"Compressible via {mechanism}."
            if confidence >= 0.30 else
            f"Marginal structure ({confidence:.3f}); gains will be small."
            if confidence >= 0.05 else
            "No exploitable structure — leave this payload alone."
        ),
    }, indent=2)


@server.tool()
def compress_payload(
    text: str,
    budget_tokens: int | None = None,
    require_lossless: bool = False,
) -> str:
    """Compress a repetitive payload down to a token budget.

    Returns the compressed text plus what it cost you. Always read `guarantee`
    and `truncated`: `lossless` means the original is reconstructable from the
    output, `lossy` means counts and salient records survive but per-event
    detail does not, and `truncated: true` means the budget forced records out
    entirely — that is data loss, not compression.

    Args:
        text: The payload to compress.
        budget_tokens: Token ceiling for the output. Omit to compress
            structurally with no cap, which is the highest-fidelity setting.
        require_lossless: Only use an extractor the payload can be rebuilt
            from. Costs ratio, buys reversibility.
    """
    records = _load(text)
    before = COUNTER.count(text)

    ctx = compress(
        records,
        CompressConfig(
            name="mcp",
            budget_tokens=budget_tokens,
            require_lossless=require_lossless,
        ),
        COUNTER,
    )
    after = COUNTER.count(ctx.text)

    header = {
        "records": ctx.records_in,
        "patterns": ctx.groups,
        "tokens_before": before,
        "tokens_after": after,
        "saved_pct": round(100 * (before - after) / before, 1) if before else 0.0,
        "extractor": ctx.stats.get("extractor"),
        "guarantee": ctx.stats.get("guarantee"),
        "truncated": ctx.stats.get("truncated", False),
        "records_verbatim": ctx.records_verbatim,
    }
    if ctx.dropped:
        header["notes"] = ctx.dropped

    return f"{json.dumps(header, indent=2)}\n\n---\n\n{ctx.text}"


def main() -> None:
    """Run the server over stdio."""
    server.run()


if __name__ == "__main__":
    main()
