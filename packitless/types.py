"""Core data types shared across packitless.

A payload is a sequence of Records. Extractors turn Records into structure,
the allocator spends a token budget across that structure, and the renderer
emits the text that actually goes into a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Record:
    """One unit of a payload — a log line, a JSON event, a stack frame group.

    Attributes:
        index: Position in the original payload. Preserved so callers can map
            compressed output back to source records.
        raw: The original text, verbatim.
        fields: Parsed structure, if the loader could extract any. Empty for
            plain line payloads.
        salience: Caller-supplied importance in [0, 1]. Records scoring high
            are candidates for verbatim inclusion. Defaults to 0.0 (unscored).
    """

    index: int
    raw: str
    fields: dict[str, Any] = field(default_factory=dict)
    salience: float = 0.0


@dataclass
class CompressedContext:
    """The result of compressing a payload.

    Attributes:
        text: The rendered text to place in a prompt.
        records_in: How many records went in.
        records_verbatim: How many survived uncompressed.
        groups: How many structural groups (templates, schemas) were found.
        dropped: Human-readable notes on what was discarded.
        stats: Extractor-specific detail, surfaced in reports.
    """

    text: str
    records_in: int
    records_verbatim: int = 0
    groups: int = 0
    dropped: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    """One (corpus, config) measurement.

    Token counts are measured, never estimated from ratios — see tokens.py.
    """

    corpus: str
    difficulty: str
    config: str
    records_in: int
    tokens_before: int
    tokens_after: int
    counter: str
    groups: int = 0
    records_verbatim: int = 0

    @property
    def saved_pct(self) -> float:
        """Percentage of input tokens removed. Negative means we made it worse."""
        if self.tokens_before == 0:
            return 0.0
        return 100.0 * (self.tokens_before - self.tokens_after) / self.tokens_before

    @property
    def ratio(self) -> float:
        """Compression ratio, before:after. 1.0 means no change."""
        if self.tokens_after == 0:
            return float("inf")
        return self.tokens_before / self.tokens_after
