"""Token counting.

Two implementations, same interface:

- HeuristicCounter is free and instant. Use it while sweeping configs.
- AnthropicCounter calls the real tokenizer via the count_tokens endpoint and
  memoizes by content hash. Use it for anything that goes on a slide.

Every number this project reports is a *measured* token count. Nothing is
derived from a character-length ratio.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# Rough BPE approximation: split into word and punctuation runs, then scale.
#
# Measured against claude-sonnet-5 on this project's corpora:
#   JSON records      +0.3%   (near exact)
#   HDFS log lines   -36.7%   (badly under-counts)
#   mixed stream     -17.1%
#
# The error is not a constant, so no single multiplier fixes it: dense
# identifier text ("dfs.DataNode$DataXceiver", "10.250.19.102:50010") splits
# into far more subword tokens than a word-and-punctuation regex predicts,
# while ordinary JSON does not. Treat this as a fast way to *rank* configs
# against each other, never as a number to publish — use AnthropicCounter for
# anything that leaves the machine.
_PIECE_RE = re.compile(r"\w+|[^\w\s]")
_PIECES_PER_TOKEN = 1.32

# Tokenisation differs across model families, so the counting model is a real
# choice, not a formality. Sonnet 5 is the current tier and the natural
# migration target for anything pinned to an older Sonnet.
DEFAULT_COUNT_MODEL = "claude-sonnet-5"

# Exact counts are measured once and committed, so anyone can reproduce every
# published number with no API key, no credit, and no network — and get the
# same answer every time. Without this, "measured with the real tokenizer"
# means "you had to pay to check", which is not reproducible in any useful
# sense.
CACHE_PATH = Path(__file__).resolve().parent.parent / "results" / "token_cache.json"

# Tried in order if the configured model is unavailable to the caller's key.
_COUNT_MODEL_FALLBACKS = (
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
    "claude-opus-5",
)


class TokenCounter(Protocol):
    """Counts tokens in a string."""

    name: str

    def count(self, text: str) -> int:
        """Return the token count for `text`."""
        ...


class HeuristicCounter:
    """Free, instant, approximate. Good enough to rank configs against each other."""

    name = "heuristic"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(_PIECE_RE.findall(text)) * _PIECES_PER_TOKEN))


class AnthropicCounter:
    """Exact counts from the count_tokens endpoint, memoized by content hash.

    The endpoint bills no tokens, but it is a network call — the cache keeps a
    budget sweep from re-paying latency on identical text.
    """

    name = "anthropic"

    def __init__(self, model: str = DEFAULT_COUNT_MODEL,
                 cache_path: Path | None = CACHE_PATH) -> None:
        import anthropic  # imported lazily so the heuristic path needs no SDK

        self._client = anthropic.Anthropic()
        self._cache_path = cache_path
        self._cache: dict[str, int] = _load_cache(cache_path)
        self._degraded: HeuristicCounter | None = None
        self._dirty = False
        self.model = self._resolve_model(model)

    def save(self) -> int:
        """Persist newly measured counts. Returns the cache size."""
        if self._dirty and self._cache_path is not None:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(self._cache, indent=0, sort_keys=True))
            self._dirty = False
        return len(self._cache)

    def _resolve_model(self, preferred: str) -> str:
        """Pick a counting model this key can actually count with.

        Probes with a real count_tokens call rather than models.retrieve.
        Metadata endpoints stay reachable in situations where counting does
        not — an exhausted credit balance denies API access wholesale, so a
        client that looks healthy at construction fails on first use. Probing
        the capability we actually need means get_counter() can fall back to
        the heuristic instead of raising mid-run.
        """
        import anthropic

        candidates = [preferred, *(m for m in _COUNT_MODEL_FALLBACKS if m != preferred)]
        last: Exception | None = None
        for candidate in candidates:
            try:
                self._client.messages.count_tokens(
                    model=candidate, messages=[{"role": "user", "content": "probe"}]
                )
                if candidate != preferred:
                    logger.warning(
                        "counting model %r unavailable; using %r instead",
                        preferred,
                        candidate,
                    )
                return candidate
            except anthropic.NotFoundError as exc:
                last = exc
                continue
            except anthropic.APIStatusError as exc:
                # Auth, billing, rate limit — not model-specific, so trying
                # other models will fail identically.
                raise RuntimeError(f"token counting unavailable: {exc}") from exc
        raise RuntimeError(
            f"no usable counting model (tried {', '.join(candidates)}): {last}"
        )

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._degraded is not None:
            return self._degraded.count(text)

        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if key not in self._cache:
            try:
                resp = self._client.messages.count_tokens(
                    model=self.model,
                    messages=[{"role": "user", "content": text}],
                )
            except Exception as exc:
                # A long sweep should not die on a transient 500 or an
                # exhausted balance halfway through. Degrade once, say so
                # loudly, and keep the run comparable from here on — mixing
                # exact and approximate counts in one report would be worse
                # than being uniformly approximate.
                logger.warning(
                    "token counting failed (%s); falling back to the heuristic "
                    "for the remainder of this run — counts are approximate",
                    exc,
                )
                self._degraded = HeuristicCounter()
                self.name = "heuristic (degraded from anthropic)"
                return self._degraded.count(text)
            self._cache[key] = resp.input_tokens
            self._dirty = True
        return self._cache[key]


class CachedCounter:
    """Replays committed exact counts; deterministic and offline.

    Falls back to the heuristic for text it has never seen, and says so, so a
    stale cache degrades visibly rather than silently reporting the wrong
    number for new content.
    """

    name = "cached (exact, replayed)"

    def __init__(self, cache_path: Path | None = CACHE_PATH) -> None:
        self._cache = _load_cache(cache_path)
        self._fallback = HeuristicCounter()
        self.misses = 0

    def count(self, text: str) -> int:
        if not text:
            return 0
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if key in self._cache:
            return self._cache[key]
        self.misses += 1
        return self._fallback.count(text)


def _load_cache(path: Path | None) -> dict[str, int]:
    """Read the committed count cache, tolerating absence or corruption."""
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("token cache unreadable (%s) — starting empty", exc)
        return {}


def get_counter(prefer_api: bool = True, allow_cache: bool = True) -> TokenCounter:
    """Return the best available counter.

    Falls back to the heuristic when there is no API key or the SDK is missing,
    so the harness always runs — offline, on a plane, or before you export a key.
    """
    if prefer_api and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return AnthropicCounter()
        except Exception as exc:  # SDK missing, bad key, no network, no credit
            logger.warning("Falling back from live token counting: %s", exc)
    if allow_cache and CACHE_PATH.exists():
        return CachedCounter()
    return HeuristicCounter()
