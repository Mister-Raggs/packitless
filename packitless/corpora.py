"""Corpus loading.

Three tiers, chosen so that compressibility varies for structural reasons
rather than by accident:

    easy    line-oriented templated logs        -> template extraction wins
    medium  homogeneous JSON records            -> schema collapse wins
    hard    several formats interleaved         -> neither wins cleanly

The hard tier exists to keep the project honest. A compressor that reports the
same ratio on all three is not measuring anything.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from packitless.types import Record

logger = logging.getLogger(__name__)

CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpora"


@dataclass
class Corpus:
    """A named payload with a difficulty label.

    Attributes:
        name: Short identifier used in reports.
        kind: "lines" | "jsonl" | "mixed". Hints the sniffer; never trusted by it.
        difficulty: "easy" | "medium" | "hard".
        records: The payload itself.
        source: Where it came from, for provenance in the writeup.
    """

    name: str
    kind: str
    difficulty: str
    records: list[Record] = field(default_factory=list)
    source: str = ""

    @property
    def text(self) -> str:
        """The uncompressed payload as it would appear in a prompt."""
        return "\n".join(r.raw for r in self.records)

    def __len__(self) -> int:
        return len(self.records)


def load_lines_text(text: str, limit: int | None = None) -> list[Record]:
    """Parse line-oriented text into records."""
    records: list[Record] = []
    for i, line in enumerate(text.splitlines()):
        if limit is not None and i >= limit:
            break
        stripped = line.rstrip("\n")
        if stripped:
            records.append(Record(index=i, raw=stripped))
    return records


def load_jsonl_text(text: str, limit: int | None = None) -> list[Record]:
    """Parse newline-delimited JSON, keeping parsed fields alongside raw text."""
    records: list[Record] = []
    for i, line in enumerate(text.splitlines()):
        if limit is not None and i >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            fields = json.loads(line)
        except json.JSONDecodeError:
            fields = {}
        records.append(Record(index=i, raw=line, fields=fields))
    return records


def load_lines(path: Path, limit: int | None = None) -> list[Record]:
    """Load a line-oriented log file."""
    return load_lines_text(
        path.read_text(encoding="utf-8", errors="replace"), limit=limit
    )


def load_jsonl(path: Path, limit: int | None = None) -> list[Record]:
    """Load newline-delimited JSON from disk."""
    return load_jsonl_text(
        path.read_text(encoding="utf-8", errors="replace"), limit=limit
    )


def load_corpus(
    name: str,
    path: Path,
    kind: str,
    difficulty: str,
    limit: int | None = None,
    source: str = "",
) -> Corpus:
    """Load one corpus from disk, dispatching on `kind`."""
    loader = load_jsonl if kind == "jsonl" else load_lines
    records = loader(path, limit=limit)
    logger.debug("loaded %s: %d records from %s", name, len(records), path)
    return Corpus(
        name=name,
        kind=kind,
        difficulty=difficulty,
        records=records,
        source=source or str(path),
    )


# The three tiers. Paths are resolved against CORPUS_DIR; see scripts/fetch_corpora.py.
REGISTRY: list[dict] = [
    {
        "name": "hdfs",
        "file": "hdfs_demo.log",
        "kind": "lines",
        "difficulty": "easy",
        "source": "Flare repo — logs/hdfs_demo.log (real HDFS cluster logs)",
    },
    {
        "name": "jobs",
        "file": "jobs.jsonl",
        "kind": "jsonl",
        "difficulty": "medium",
        "source": "JobHunter production DB — 33,626 scraped postings",
    },
    {
        "name": "mixed",
        "file": "mixed.log",
        "kind": "mixed",
        "difficulty": "hard",
        "source": "hdfs + jobs interleaved — simulates a multi-service stream",
    },
]


def discover(limit: int | None = None) -> list[Corpus]:
    """Load every registered corpus that is present on disk."""
    found: list[Corpus] = []
    for spec in REGISTRY:
        path = CORPUS_DIR / spec["file"]
        if not path.exists():
            logger.warning("corpus %s missing at %s — skipping", spec["name"], path)
            continue
        found.append(
            load_corpus(
                name=spec["name"],
                path=path,
                kind=spec["kind"],
                difficulty=spec["difficulty"],
                limit=limit,
                source=spec["source"],
            )
        )
    return found
