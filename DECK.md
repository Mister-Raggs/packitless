# packitless — 3-minute demo

**Track 1 · Cost of Intelligence**

Repo: `github.com/Mister-Raggs/packitless` · Results page: *(artifact link — **share it before submitting**)*

Six beats, ~30 seconds each. Lead with where it fits and what it saves; the evidence is
there to be pointed at, not walked through.

---

## 1 — The pitch (0:00–0:25)

> **Machine-generated text is mostly repetition, and you pay full price for every copy.**

Logs, traces, events, test output, database rows. packitless extracts the structure,
spends a token budget on what carries signal, and tells you what it gave up to get there.

Open on the money figure:

> **"$3,718 saved per thousand calls, across six real workloads, at list price."**

Then immediately ground it — that's input tokens only, priced at $3/MTok, and every
number on the page is measured rather than modelled.

---

## 2 — Six places it fits (0:25–1:00)

The scenario grid. Don't read all six — point at the spread and land three:

| Scenario | Payload | Saved | Per 1k calls |
|---|---|---|---|
| Agent reading build output | installer log | **99.8%** | $667 → $1.31 |
| Incident triage (Flare, production) | HDFS logs | **99.5%** | $443 → $2.39 |
| Batch record processing | JSON rows | 27.0% *(lossless)* | $1,631 → $1,191 |
| Multi-service pipeline | mixed stream | 95.8% | $823 → $35 |
| A log format nobody has seen | WiFi driver | **99.9%** | $1,384 → $1.89 |
| Prose | this README | **0%** — declines | unchanged |

**The three to say out loud:**

- *"An agent reading build output: 99.8%."* — the one this audience feels.
- *"Batch records: only 27%, but it's **lossless** — the original is reconstructable."*
  Naming the weakest number yourself is what makes the others credible.
- *"Prose: zero. It declines."* — knowing when not to act is what makes it safe to adopt.

---

## 3 — Where the savings come from (1:00–1:35)

Treemap. Two thousand HDFS lines, **zero exact duplicates** — deduplication saves nothing —
collapsing into 13 templates.

> *"Nothing here is a duplicate. Everything here is a repeat."*

That distinction kills "why not just dedupe?" before anyone asks it.

Then the breakdown bar — every surviving token, accounted for:

```
patterns   341 tok   the structural spine
verbatim    78 tok   records we refused to drop
header      29 tok
           ─────
           438 tok   from 222,336
```

> *"98% saved is a claim. This is the explanation."*

The interface is a **token budget**, not a compression ratio, because real systems have
context windows and cost ceilings — not ratio targets.

---

## 4 — What it costs you in quality (1:35–2:05)

Judge always sees the **uncompressed** logs as ground truth, and rates every candidate
for an incident **side by side in one call**.

**94% fewer tokens ranks level with no compression at all** — 4.17 vs 4.08, rank 2.50 vs
2.25. It wins on *specificity* (4.25 vs 3.88): templates plus counts plus the salient
lines surface structure an 18,000-token raw dump buries.

**Say the caveat:** sd ≈ 0.5 at n=8, so that's a *tie*, not a win. Below ~800 tokens
quality genuinely degrades and the chart shows it.

**And say this:** the first version of this experiment scored each summary alone and
reported the curve as flat noise. The instrument was the problem, not the compressor —
the flattering result was the wrong one.

---

## 5 — When our own number is a lie (2:05–2:35)

The slide that separates this from a benchmark.

| corpus | budget | saved | records kept |
|---|---|---|---|
| jobs | 200 | **90.5%** | **0.001** |

> *"Our own tool reports 90% saved here. It got that by throwing away 99.9% of the
> records. The token count calls it a triumph."*

Deterministic fidelity checks catch it — no API key, no LLM, on every commit.
**Critical recall is 1.000 in every row:** flagged records survive whatever the budget,
and the allocator overruns rather than drop one.

Two more guarantees worth a sentence each:

- **Lossless tier** — 24,000 fields rebuilt exactly from the compressor's own output.
- **Never inflates** — pointed at our own README an earlier build returned *22% more*
  tokens than it received. Now it detects that and passes through. Caught by a test.

---

## 6 — How you'd actually use it (2:35–3:00)

Three surfaces, one function underneath:

```python
compress(records, CompressConfig(budget_tokens=800))     # library
```
```bash
kubectl logs deploy/api | packitless --budget 800 --stats  # any tool, any language
```
```
MCP: analyse → compress_payload                            # the agent decides
```

Close on adoption cost: **no API key, no service, no network.** Compression never makes
a model call. Adding a new payload format is three methods.

---

## If asked

**"Isn't this just Drain / log parsing?"**
Drain is one extractor. The system is the budget allocator, the salience guarantee, the
per-record router for mixed streams, and the checks that tell you when a number is a lie.

**"What predicts how well it does?"**
Not "is it a log". *Structure* picks the mechanism; *cardinality* sets the ratio. The
JSON corpus has perfect structure — one schema over 4,000 records — and still reaches
only 27%, because its values are high-cardinality.

**"Are the numbers reproducible?"**
Exact token counts are cached and committed, so anyone reproduces every figure offline
with no key. `pytest tests/` runs the guarantees; `fidelity_sweep.py` runs the free
frontier.

**"What did the evidence cost?"**
The first sweep cost ~$4, and most of that was our own mistake — it re-sent the same
18,481-token payload 48 times without caching. A token-reduction tool measured by a
harness that ignored the obvious token optimisation. Batching the judge cut it ~5× and
made it sharper.

**"What's not done?"**
Larger n on the frontier — 8 incidents is what a 2,000-line corpus yields. Streaming
compression for unbounded inputs. A hosted endpoint for the paste box, which is
deliberately client-side so pasted production logs never leave the browser.

---

## Do not

- Don't walk the architecture diagram. Nobody scores it.
- Don't quote a raw dollar total — always per 1,000 calls, at a stated rate.
- Don't claim a knee in the frontier. There's a slope, not a knee, at this sample size.
- Don't run anything live that needs the network. The paste box is local; use that.
