"""The guarantees packitless makes, enforced on every commit.

Fixtures are synthetic and self-contained — no corpora, no API key, no
network — so these run anywhere. They encode the bugs that actually happened
during development rather than hypothetical ones:

    a pipe inside a value silently shifted every later column
    truncated dictionaries made "lossless" a false claim
    a shared Drain miner made results drift between identical runs
    records were dropped for budget without anyone being told

Each of those shipped at some point and was caught by measurement, not by
reading the code.
"""

from __future__ import annotations

import json

import pytest

from packitless.allocator import allocate
from packitless.compress import CompressConfig, compress
from packitless.corpora import load_jsonl_text, load_lines_text
from packitless.extractors import select
from packitless.extractors.jsonrec import decode_rendered
from packitless.fidelity import measure
from packitless.render import render
from packitless.salience import score_records
from packitless.tokens import HeuristicCounter

COUNTER = HeuristicCounter()

# Values chosen to break a naive delimited encoder.
NASTY_VALUES = [
    "Senior ML Engineer| Uber Direct",   # the row delimiter
    "  leading and trailing spaces  ",   # stripped by a careless parser
    r"back\slash and \| escaped pipe",   # escape-character interaction
    "unicode: café — naïve ✓",           # non-ascii
    "",                                   # empty
]


def make_records(n: int = 200) -> str:
    """Synthetic JSONL with repeating low-cardinality columns."""
    rows = []
    for i in range(n):
        rows.append(json.dumps({
            "service": ["api", "worker", "cron"][i % 3],
            "region": ["us-east", "eu-west"][i % 2],
            "title": NASTY_VALUES[i % len(NASTY_VALUES)],
            "seq": str(i),
        }, separators=(",", ":")))
    return "\n".join(rows)


def make_logs(n: int = 400) -> str:
    """Synthetic templated logs with exactly one anomaly."""
    lines = [
        f"2026-08-07 10:{i // 60:02d}:{i % 60:02d} INFO worker[{i}] "
        f"processed batch blk_{100000 + i} in {i % 90}ms"
        for i in range(n)
    ]
    lines.insert(n // 2, "2026-08-07 10:03:11 ERROR worker[7] "
                         "Exception in receiveBlock blk_999999: Broken pipe")
    return "\n".join(lines)


def compress_parts(records, budget):
    """Run the pipeline, returning the pieces fidelity needs."""
    config = CompressConfig(name="test", budget_tokens=budget)
    extractor, _ = select(records)
    structure = extractor.extract(records)
    scores = score_records(records, structure)
    plan = allocate(
        records=records, structure=structure, scores=scores, counter=COUNTER,
        budget_tokens=budget, verbatim_floor=config.verbatim_floor,
        max_verbatim=config.max_verbatim,
    )
    return structure, plan, scores, render(records, structure, plan, scores)


class TestLossless:
    """A structure claiming reconstructable must actually reconstruct."""

    def test_round_trip_is_exact(self):
        records = load_jsonl_text(make_records())
        # require_lossless matters here: unconstrained, the competition can
        # pick line templating, which scores higher and discards every value.
        ctx = compress(
            records, CompressConfig(name="t", require_lossless=True), COUNTER
        )
        assert ctx.stats["guarantee"] == "lossless"

        rebuilt = decode_rendered(ctx.text)
        originals = [json.loads(r.raw) for r in records]

        assert len(rebuilt) == len(originals)
        for want, got in zip(originals, rebuilt):
            assert got == want

    @pytest.mark.parametrize("value", NASTY_VALUES)
    def test_delimiter_and_escape_survive(self, value):
        """A pipe in a value once shifted every later column, corrupting URLs."""
        text = "\n".join(
            json.dumps({"a": value, "b": "constant", "n": str(i)}) for i in range(60)
        )
        ctx = compress(
            load_jsonl_text(text),
            CompressConfig(name="t", require_lossless=True), COUNTER,
        )
        rebuilt = decode_rendered(ctx.text)
        assert all(r["a"] == value for r in rebuilt)

    def test_dictionaries_are_complete(self):
        """Truncating a dictionary for display makes references unresolvable."""
        records = load_jsonl_text(make_records())
        ctx = compress(
            records, CompressConfig(name="t", require_lossless=True), COUNTER
        )
        for row in decode_rendered(ctx.text):
            assert not row["service"].startswith("@")
            assert not row["region"].startswith("@")


class TestCriticalRecall:
    """Compression may drop routine repetition, never the flagged record."""

    @pytest.mark.parametrize("budget", [None, 800, 400, 200, 100])
    def test_anomaly_survives_every_budget(self, budget):
        records = load_lines_text(make_logs())
        structure, plan, scores, rendered = compress_parts(records, budget)
        fid = measure(records, structure, plan, scores, rendered)
        assert fid.critical_recall == 1.0, (
            f"a flagged record was dropped at budget={budget}"
        )

    def test_error_line_is_scored_above_routine(self):
        records = load_lines_text(make_logs())
        _, _, scores, _ = compress_parts(records, None)
        error = next(r for r in records if "Exception" in r.raw)
        assert scores[error.index] > max(
            s for i, s in scores.items() if i != error.index
        )


class TestHonesty:
    """Losing data is allowed. Losing it quietly is not."""

    def test_truncation_is_reported(self):
        records = load_jsonl_text(make_records(400))
        ctx = compress(
            records,
            CompressConfig(name="t", budget_tokens=200, require_lossless=True),
            COUNTER,
        )
        assert ctx.stats["truncated"] is True
        # Truncation downgrades the guarantee even for a reconstructable
        # extractor — you cannot rebuild what was not emitted.
        assert ctx.stats["guarantee"] == "lossy"
        assert ctx.stats["rows_omitted"] > 0
        assert any("dropped for budget" in note for note in ctx.dropped)

    def test_unbudgeted_run_loses_nothing(self):
        for text, loader in ((make_logs(), load_lines_text),
                             (make_records(), load_jsonl_text)):
            records = loader(text)
            structure, plan, scores, rendered = compress_parts(records, None)
            fid = measure(records, structure, plan, scores, rendered)
            assert fid.passed, "unbudgeted compression must not lose information"


class TestScope:
    """Payloads with no structure must pass through, not be mangled."""

    def test_prose_passes_through_unchanged(self):
        # Genuinely varied text. An earlier version of this fixture differed
        # only by an integer, which masks to <NUM> — making "prose" perfectly
        # templated and the tool right to compress it.
        vocab = ("harbour lantern drift copper meadow signal thistle bramble "
                 "cinder verge quarry fathom lichen tumult gable kestrel "
                 "furrow slate ember tallow").split()
        # A unique alphabetic token per line guarantees no two lines collapse
        # to the same template. Digits would mask to <NUM> and reintroduce the
        # collisions this fixture exists to avoid.
        def tag(i):
            return chr(ord("a") + i // 26) + chr(ord("a") + i % 26)
        prose = "\n".join(
            f"{tag(i)} " + " ".join(
                vocab[(i * 11 + j * 5) % len(vocab)] for j in range(i % 9 + 6)
            )
            for i in range(80)
        )
        records = load_lines_text(prose)
        ctx = compress(records, CompressConfig(name="t"), COUNTER)
        assert ctx.stats["extractor"] == "passthrough"
        assert ctx.text == prose


class TestDeterminism:
    """A shared Drain miner once made results drift between identical runs."""

    def test_repeated_compression_is_identical(self):
        records = load_lines_text(make_logs())
        outputs = {
            compress(records, CompressConfig(name="t", budget_tokens=600), COUNTER).text
            for _ in range(5)
        }
        assert len(outputs) == 1

    def test_partition_indexing_is_position_independent(self):
        """Extractors must key on Record.index, not list position."""
        mixed = make_logs(120) + "\n" + make_records(60)
        records = load_lines_text(mixed)
        ctx = compress(records, CompressConfig(name="t"), COUNTER)
        assert ctx.records_in == len(records)
        assert ctx.stats["extractor"] == "hybrid"
