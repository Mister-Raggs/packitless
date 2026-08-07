# packitless

Budget-aware context compression for repetitive LLM payloads.

**[Measured results](https://mister-raggs.github.io/packitless/)** ·
**[Slides](https://mister-raggs.github.io/packitless/slides.html)**

Machine-generated text — logs, traces, events, structured records — is mostly
repetition, and you pay full token price for every copy of it. `packitless`
extracts the structure, spends a token budget on what actually carries signal,
and tells you which guarantee it managed to give you.

```python
from packitless import compress, CompressConfig

ctx = compress(records, CompressConfig(name="tight", budget_tokens=800))
prompt = TEMPLATE.format(payload=ctx.text)
```

```bash
kubectl logs deploy/api --tail=5000 | packitless --budget 800 --stats
# 5,000 records · 34 patterns · 147,815 → 1,724 tok (98.8% ↓) · lines/0.963 · lossy
```

## Where it earns its keep

Six real payloads, measured at build time. Input tokens only, priced at $3.00 per
million (claude-sonnet-5 list), projected over 1,000 calls:

| Scenario | Payload | Saved | Per 1k calls |
|---|---|---|---|
| Agent reading build output | installer log | **99.8%** | $667 → $1.31 |
| Incident triage (Flare) | HDFS logs | **99.5%** | $443 → $2.39 |
| Batch record processing | JSON rows | 27.0% **lossless** | $1,631 → $1,191 |
| Multi-service pipeline | mixed stream | 95.8% | $823 → $35 |
| Unseen log format | WiFi driver | **99.9%** | $1,384 → $1.89 |
| Prose | this README | **0% — declines** | unchanged |

Exact token counts are cached and committed, so every figure reproduces offline
with no API key.

## Measured results

Real tokenizer (`claude-sonnet-5`), no budget cap, so nothing is truncated:

| Corpus | Extractor | Tokens in | Tokens out | Saved | Guarantee |
|---|---|---|---|---|---|
| HDFS logs (2,000 lines) | `lines` | 147,815 | 1,724 | **98.8%** | lossy |
| JSON records (4,000) | `jsonrec` | 543,687 | 396,863 | **27.0%** | **lossless** |
| Mixed stream (3,000) | `hybrid` | 274,428 | 99,639 | **63.7%** | lossy |

### Generalisation — logs the tool was never tuned against

| Log | Records | Patterns | Saved |
|---|---|---|---|
| `wifi.log` | 4,000 | 48 | 98.5% |
| `fsck_apfs.log` | 903 | 11 | 97.4% |
| `install.log` | 4,000 | 243 | 94.6% |
| `DiscRecording.log` | 356 | 2 | 92.9% |
| `LeagueClientUx_debug.log` | 224 | 54 | 78.5% |

**median 94.6% · mean 92.4% · range 78.5–98.5%**

### Does compression hurt the answer?

Log summarisation, scored by an independent LLM judge that always sees the
**uncompressed** logs as ground truth and rates every candidate for an incident
side by side in one call (n=8 incidents):

| Level | Payload tok | Saved | Mean | sd | Rank |
|---|---|---|---|---|---|
| baseline | 18,481 | — | 4.08 | 0.56 | 2.25 |
| budget=None | 1,107 | **94.0%** | **4.17** | 0.47 | 2.50 |
| budget=800 | 818 | 95.6% | 3.88 | 0.47 | 3.62 |
| budget=400 | 398 | 97.8% | 3.62 | 0.60 | 4.12 |
| budget=200 | 200 | 98.9% | 3.33 | 0.76 | 4.88 |

At 94% compression the result ranks level with the uncompressed baseline — a
tie, not a win, given sd ≈ 0.5 at n=8. Below ~800 tokens quality degrades, and
rank orders monotonically.

An earlier version scored each summary in isolation and reported the curve as
flat noise. The instrument was the problem, not the compressor; it also cost 5x
more, because it re-sent the same 18,481-token payload on every call.

## Two guarantees, and it tells you which one you got

- **`lossless`** — the payload is reconstructable from the output alone.
  Proven, not asserted: `scripts/verify_lossless.py` rebuilds all 4,000
  records field-by-field from the rendered text.
- **`lossy`** — distribution, counts, and the most salient records survive;
  per-event parameter values do not. This is the tier the judge measures.

If a budget forces records or patterns out of the output, the result is
labelled `truncated` and says how many were dropped. Compression that silently
discards data is indistinguishable from a bug.

It also **never returns more tokens than it was given**. Pointed at this README
an earlier build returned 22% *more* — prose scores just above the confidence
floor, so it compressed, and the pattern list cost more than the lines it
replaced. It now detects that and passes through.

## How it works

```
payload → sniff competition → extractor → salience → budget allocator → render
```

- **Sniff** is a compressibility estimate, not format detection. Each extractor
  reports how well it expects to do; highest score wins. Point it at prose and
  every extractor scores ~0, so the payload passes through untouched.
- **Extractors** implement three methods — `sniff`, `claim`, `extract`. Adding
  a payload format is ~40 lines.
- **`hybrid`** routes *per record*, so a stream carrying several formats gets
  each one handled by the extractor that understands it. It scores 0.0 on
  homogeneous payloads and steps aside.
- **Salience** decides what survives: rarity (log-scaled group size), severity
  vocabulary, or a caller-supplied score. Records above `verbatim_floor` are
  kept even if that means overrunning the budget — and the overrun is reported.

## Install and reproduce

```bash
uv venv && uv pip install -e .

# Corpora are not committed — rebuild them from their sources
python scripts/build_corpora.py --flare-repo ../flare --jobs-db path/to/jobs.db

pytest tests/                         # the guarantees — no key, no network
python scripts/fidelity_sweep.py      # free frontier: savings vs information loss
python scripts/verify_lossless.py     # round-trip proof
python scripts/baseline.py            # per-corpus baselines
python scripts/unseen_benchmark.py /var/log/*.log

# Needs API credit; the batched judge costs roughly a fifth of the original
python scripts/frontier_batched.py --flare-repo ../flare
```

Rebuild the published page and deck after any change:

```bash
python scripts/export_report.py --exact   # only if measurements changed
python scripts/build_page.py              # regenerates results/ and docs/
```

`ANTHROPIC_API_KEY` is optional. Without it the token counter falls back to a
free heuristic — good enough to rank configs, but it under-counts dense
identifier text by ~37%, so use `--exact` for any number you intend to publish.

## Three ways in

**Library** — one call in whatever builds your prompt.

```python
compress(records, CompressConfig(name="tight", budget_tokens=800))
```

**CLI** — reads stdin, writes to stdout, stats to stderr, so it drops into a
pipeline without disturbing it. Works with any tool in any language.

```
packitless [--budget N] [--extractor NAME] [--stats] [--json] [--explain] [--exact]
```

**MCP** — `packitless-mcp` exposes `analyse` and `compress_payload`, so an agent
can compress tool output before reading it into context. `analyse` is free and
read-only, because the right answer is sometimes "don't bother".
