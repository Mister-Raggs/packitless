"""Extractor interface.

An extractor recovers structure from a payload: templates from line logs,
a shared schema from JSON records, repeated substrings from anything else.

Two methods matter:

    sniff()    cheap probe — "how well would I do on this?" Returns a
               confidence in [0, 1]. The registry picks the highest scorer,
               so format detection is really a compressibility estimate.

    extract()  the real pass over the payload.

Because sniff() measures expected compression rather than guessing a format
by eye, an unknown payload degrades to whichever extractor genuinely helps
most instead of failing outright.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from packitless.types import Record

# Records sampled by sniff(). Enough to estimate a ratio, small enough to be free.
SNIFF_SAMPLE = 300


@dataclass
class Group:
    """A set of records sharing one structural pattern.

    Attributes:
        label: Short id used in rendered output, e.g. "T1".
        pattern: The template or schema signature, with variables masked.
        members: Indices into the original record list.
        exemplar: One representative record, kept for verbatim rendering.
    """

    label: str
    pattern: str
    members: list[int] = field(default_factory=list)
    exemplar: Record | None = None

    @property
    def count(self) -> int:
        return len(self.members)


@dataclass
class Structure:
    """What an extractor recovered.

    Attributes:
        extractor: Which extractor produced this.
        groups: Structural groups, largest first.
        legend: Shared context the renderer emits once, e.g. JSON keys.
        notes: Human-readable observations for the report.
        estimated_savings: Extractor's own estimate of the fraction of the
            payload it can remove, in [0, 1]. Set this when group count is a
            poor proxy — schema collapse yields one group covering every
            record, which would otherwise read as ~100% compressible.
        compact_rows: Per-record compressed representations, as
            (record index, text). Extractors whose savings come from encoding
            each record more cheaply — rather than from collapsing records
            into templates — must populate this. Leaving it empty means "the
            group summaries carry all the information", which is true for
            line templating and false for schema collapse.
        reconstructable: True when the original payload can be rebuilt from
            this structure alone. Schema collapse is reconstructable — keys
            and dictionary references restore every record. Line templating
            is not: it keeps what happened and how often, but discards the
            parameter values of individual events. The distinction decides
            whether a claim needs a fidelity eval or can simply be proven.
    """

    extractor: str
    groups: list[Group] = field(default_factory=list)
    legend: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    estimated_savings: float | None = None
    compact_rows: list[tuple[int, str]] = field(default_factory=list)
    reconstructable: bool = False

    @property
    def coverage(self) -> int:
        """How many records fell into some group."""
        return sum(g.count for g in self.groups)

    def compression_estimate(self, total: int) -> float:
        """Fraction of the payload this structure can remove.

        Uses the extractor's own estimate when it set one; otherwise falls
        back to how far the record count collapsed into patterns.
        """
        if self.estimated_savings is not None:
            return self.estimated_savings
        if total == 0:
            return 0.0
        return 1.0 - (len(self.groups) / total)


class Extractor(Protocol):
    """Recovers structure from a payload."""

    name: str

    def sniff(self, records: list[Record]) -> float:
        """Estimate how well this extractor would compress `records`."""
        ...

    def claim(self, record: Record) -> float:
        """How well this extractor handles a *single* record, in [0, 1].

        `sniff` answers "should I own this payload?"; `claim` answers "should
        I own this record?". The difference matters on heterogeneous streams,
        where one payload holds several formats and a whole-payload winner
        mangles everything it did not expect. The router uses `claim` to
        partition records before extracting.

        Return a low non-zero baseline if the extractor can process anything
        (poorly), and near 1.0 only for records it genuinely understands.
        """
        ...

    def extract(self, records: list[Record]) -> Structure:
        """Recover structure from the full payload."""
        ...
