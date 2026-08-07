# packitless

Budget-aware context compression for repetitive LLM payloads.

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
**uncompressed** logs as ground truth (n=8 incidents, 48 runs):

| Level | Payload tok | Saved | Relevance | Mean quality |
|---|---|---|---|---|
| baseline | 18,481 | — | 5.00 | 4.46 |
| budget=800 | 818 | 95.6% | 5.00 | 4.25 |
| budget=200 | 200 | 98.9% | 5.00 | 4.33 |

Relevance holds at 5.00 throughout — summaries never became *wrong*. Quality
differences between budgets are within noise at this sample size, so the
honest claim is "no detectable quality loss down to 200 tokens", not "we found
the optimum".

## Two guarantees, and it tells you which one you got

- **`lossless`** — the payload is reconstructable from the output alone.
  Proven, not asserted: `scripts/verify_lossless.py` rebuilds all 4,000
  records field-by-field from the rendered text.
- **`lossy`** — distribution, counts, and the most salient records survive;
  per-event parameter values do not. This is the tier the judge measures.

If a budget forces records or patterns out of the output, the result is
labelled `truncated` and says how many were dropped. Compression that silently
discards data is indistinguishable from a bug.

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

python scripts/baseline.py            # per-corpus baselines
python scripts/verify_lossless.py     # round-trip proof
python scripts/unseen_benchmark.py /var/log/*.log
python scripts/frontier.py --flare-repo ../flare   # needs API credit
```

`ANTHROPIC_API_KEY` is optional. Without it the token counter falls back to a
free heuristic — good enough to rank configs, but it under-counts dense
identifier text by ~37%, so use `--exact` for any number you intend to publish.

## CLI

```
packitless [--budget N] [--extractor NAME] [--stats] [--json] [--explain] [--exact]
```

Reads stdin, writes compressed text to stdout, stats to stderr — so it drops
into a pipeline without disturbing it.
