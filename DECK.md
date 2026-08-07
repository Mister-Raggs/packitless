# packitless — 3-minute demo

**Track 1 · Cost of Intelligence**

Repo: `github.com/Mister-Raggs/packitless` · Results page: *(artifact link — share before submitting)*

Timing is tight. Six slides, ~30 seconds each. The numbers are the argument; don't
narrate the architecture.

---

## 1 — The problem (0:00–0:25)

> Machine-generated text is mostly repetition, and you pay full token price for every
> copy of it.

Show the treemap. Two thousand HDFS log lines, **zero exact duplicates** — so
deduplication saves nothing — collapsing into 13 templates.

**Line to land:** *"Nothing here is a duplicate. Everything here is a repeat."*

That distinction is the whole product, and it kills "why not just dedupe?" before
anyone asks it.

---

## 2 — What it does (0:25–0:55)

Before/after panel, real output on screen.

```
── 2,000 records · 13 patterns · extractor=lines ──
[T1] ×1,149   081109 <*> INFO dfs.DataNode$DataXceiver: Receiving block <*> src: <*> dest: <*>
[T2] ×404     081109 <*> INFO dfs.DataNode$DataXceiver: <*> Served block <*> to <*>
...
verbatim (highest salience):
  [0.97] 081109 ... ERROR DataXceiver: Exception in receiveBlock ... Broken pipe
```

**147,815 → 1,724 tokens. 98.8%.**

Point at the verbatim line: the interface is a **token budget**, not a compression
ratio, because real systems have context windows and cost ceilings — not
ratio targets. Records you flag as critical survive even if that overruns the budget.

---

## 3 — Does it hurt the answer? (0:55–1:30)

Frontier chart. Judge always sees the **uncompressed** logs as ground truth, so a
summary that lost something has nowhere to hide.

**Relevance holds at 5.00 at every budget.** Summaries never became wrong.

**Say the caveat out loud:** the curve is flat *and non-monotonic* — 200 tokens scored
higher than 800. At n=8 on an integer rubric that's noise. The claim is "no detectable
quality loss down to 200 tokens," not "we found the optimum."

Volunteering that is worth more than hiding it. It signals you know what your
instrument can and cannot resolve.

---

## 4 — The number that isn't real (1:30–2:00)

This is the slide that separates the project from a benchmark.

| corpus | budget | saved | records kept |
|---|---|---|---|
| jobs | 200 | **90.5%** | **0.001** |

> "Our own tool reports 90% saved here. It got that by throwing away 99.9% of the
> records. The token count calls it a triumph."

Free deterministic fidelity metrics catch it — no API key, no LLM, runs on every
commit. And `critical_recall` is **1.000 across every corpus and every budget**: the
must-keep guarantee is verified, not asserted.

The lossless tier goes further — 24,000 fields rebuilt exactly from the compressor's
own output.

---

## 5 — Does it generalise? (2:00–2:30)

Two answers, both measured.

**Unseen logs:** five formats pulled off a laptop, no tuning — Apple installer, WiFi
driver, APFS check, disc-recording daemon, a game client. **Median 94.6%.** Weakest
result shown, not trimmed.

**Where it declines:** point at the heatmap. Prose and source code score ~0 and pass
through *untouched*. Selection is a compressibility estimate, not format detection, so
it fails safe instead of mangling text it doesn't understand.

If there's a laptop in the room: `pytest -v | packitless --stats` live. Unfakeable.

---

## 6 — It's real (2:30–3:00)

Dropped into Flare, a production log-triage service:

| | tokens | lines the model sees |
|---|---|---|
| Flare today | 4,173 | 50 of 250 |
| packitless @ 800 | **786** | **all 250** |

> "81% fewer tokens on five times the data. Flare truncates to 50 lines to control
> cost — that's a budget in the wrong unit."

Close on the integration surfaces: a library call, a stdin filter that works with any
tool in any language, and an extractor interface where a new payload format is three
methods.

---

## If asked

**"Isn't this just Drain / log parsing?"**
Drain is one extractor. The system is the budget allocator, the salience guarantee,
the per-record router for mixed streams, and the fidelity metrics that tell you when
a number is a lie.

**"What about non-log data?"**
The JSON tier is non-log and lossless — 27%, round-trip verified. The predictor isn't
"is it a log", it's *structure* (which mechanism applies) and *cardinality* (what ratio
you get). `jobs` has perfect structure and still only reaches 27%, because its values
are high-cardinality.

**"What did it cost to build the evidence?"**
About $4, and most of that was our own mistake — the eval harness re-sent the same
18,481-token payload 48 times without caching. A token-reduction tool measured by a
harness that ignored the obvious token optimisation. The fix takes a sweep to ~$0.30,
and the free fidelity tier means most runs now cost nothing.

**"What's not done?"**
Batched-judge frontier at larger n. An MCP server. CI running the round-trip on a
synthetic fixture. The routing fix landed today; per-record routing took the mixed
stream from 835 garbage patterns to 14 clean ones.

---

## Do not

- Don't walk through the architecture diagram. Nobody scores it.
- Don't claim a knee in the frontier. There isn't one at this sample size.
- Don't show a raw dollar total — per-incident spend is cents. Quote a rate
  (cost per 1,000 incidents) or a percentage.
- Don't run anything live that needs the network.
