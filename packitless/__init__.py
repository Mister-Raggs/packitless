"""packitless — budget-aware context compression for repetitive LLM payloads.

Give it a payload and a token budget; it returns a compact, human-readable
context that preserves the records you said you cannot afford to lose.

    from packitless import compress, CompressConfig

    ctx = compress(records, CompressConfig(name="tight", budget_tokens=400))
    prompt = TEMPLATE.format(payload=ctx.text)
"""

from packitless.compress import PASSTHROUGH, CompressConfig, compress
from packitless.corpora import Corpus, discover, load_corpus
from packitless.tokens import HeuristicCounter, TokenCounter, get_counter
from packitless.types import CompressedContext, Record, RunResult

__version__ = "0.1.0"

__all__ = [
    "CompressConfig",
    "CompressedContext",
    "Corpus",
    "HeuristicCounter",
    "PASSTHROUGH",
    "Record",
    "RunResult",
    "TokenCounter",
    "compress",
    "discover",
    "get_counter",
    "load_corpus",
]
