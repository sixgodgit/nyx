# Déjà Vu: Treating Retrieval Failure as a Signal

**English** | [简体中文](dejavu.zh-CN.md)

> A perception layer for agent memory — so your agent knows it **used to know**
> something, even when it can't find it.

---

## The problem: recall systems are missing a state

A user types:

> What was the name of that Sichuan place we talked about?

The agent searches, gets nothing, and replies:

> I don't have anything about a Sichuan restaurant.

But the user *did* mention it — they said "Zhang mentioned a shop doing Sichuan food."
Different words, same place.

To any retriever, this is indistinguishable from never having discussed it at all:

| Reality | Vector search | BM25 / FTS5 |
|---|---|---|
| Never happened | top-k returned, all irrelevant | 0 results |
| Happened, but not found | top-k returned, all irrelevant | 0 results |

Vector search always returns *something* — which is arguably worse, because a
plausible-looking answer hides "I didn't actually find it."

**No mainstream approach distinguishes these two cases.** That's a systematic
failure class in recall.

---

## Why existing systems can't cover this

Not because they're bad — because they're solving a different problem.

- **Vector search** optimizes *ranking when you can find it*. Its output space has no "I didn't find it" state; top-k is always k.
- **BM25 / FTS** optimizes term matching. No match means zero results, with no signal that a paraphrase might exist.
- **Graph memory (cognee, Zep, …)** optimizes relational reasoning — but you must *hit an entity first* to walk the graph. No hit, no walk.

They all answer *"what did I find?"* Nobody answers *"have I seen this before?"*

---

## Design: two layers, two jobs

The core bet: **"have I encountered this?" can be modeled independently of
"can I retrieve it?" — and it can be made extremely cheap.**

### Layer 1 — Veil (Bloom filter)

A fixed 1,048,576-bit bitmap (128 KiB) with 7 hash functions.

- Write: set 7 bits per token
- Read: check whether all 7 are set

It answers exactly one question: *have I seen this token?*

**Why Bloom instead of an exact set?**
An exact set must store every token verbatim. For millions of memories that's far
beyond 128 KiB, plus serialization and dedup work. Bloom covers **any scale in a
fixed 128 KiB** — the cost is false positives.

**Why are false positives acceptable?**
This layer *routes*, it doesn't adjudicate:

- False positive → one extra Mist lookup → empty → degrades to "no." Cost: **one extra SQLite query.**
- False negative → **impossible** (Bloom guarantees this).

So the price of a false positive is **performance, not correctness.** The trade holds.

### Layer 2 — Mist (SQLite)

For tokens Veil reports as seen, Mist records the traces: first/last sighting,
count, caller-supplied reference, and a text snippet. This is the "oh right, now I remember" part.

```
query ──► tokenize ──► Veil probe ──┬─► all unseen ──► "never discussed" (definite)
                                    │
                                    └─► some seen ──► Mist hunt ──┬─► traces found
                                                                  └─► familiar, but no details
```

That last branch — **familiar, but can't recall details** — is the human
tip-of-the-tongue state. This is the first time a system can express it.

---

## Measurements

> Every number below is measured by [`benchmarks/dejavu_bench.py`](../benchmarks/dejavu_bench.py).
> Reproduce with `python3 benchmarks/dejavu_bench.py`. Raw data:
> [`benchmarks/results/dejavu_bench.json`](../benchmarks/results/dejavu_bench.json).
>
> Re-measured on 2026-10-27 with the v7.16.2 tokenizer, on a different machine from the earlier runs. Latency and
> ms/memory are far lower than the previously published numbers (imprint p50 3.2 → 0.38 ms) **because of the machine,
> not the code**: on this machine the v7.16.1 and v7.16.2 tokenizers measure imprint p50 0.37 vs 0.34 ms, sense 0.07
> vs 0.06 ms.

### False-positive rate: measured vs theory

| Unique tokens | Measured FP | Theoretical FP | Fill ratio | Bitmap |
|---|---|---|---|---|
| 1,000 | 0.00000 | 0.00000 | 0.0066 | 131,072 B |
| 10,000 | 0.00000 | 0.00000 | 0.0646 | 131,072 B |
| 100,000 | 0.00659 | 0.00650 | 0.4865 | 131,072 B |
| 1,000,000 | 0.99086 | 0.99121 | 0.9987 | 131,072 B |

End-to-end (full `imprint`, including Mist writes):

| Memories | Unique tokens | Measured FP | ms/memory |
|---|---|---|---|
| 1,000 | 35,213 | 0.00000 | 1.35 |
| 10,000 | 351,999 | 0.17810 | 1.77 |

### The knee: when you must grow the bitmap

From that table, the effective capacity of a 1M-bit bitmap:

| Target FP rate | Max unique tokens |
|---|---|
| 1% | ~109,000 |
| 5% | ~158,000 |
| 10% | ~190,000 |

**What matters is unique token count, not memory count.** How many tokens a
memory yields depends on language and tokenizer:

| Corpus | Unique tokens / memory | Memories until 1% FP |
|---|---|---|
| Real Chinese chit-chat (LCCC, 2,660 utterances, `benchmarks/dejavu_chinese_real.py`) | ~4.1 (5.5 with the v7.16.1 tokenizer) | **~26,600** |
| Real Chinese conversations (2,660 local messages, v7.16.1 tokenizer) | ~6.1 | ~18,000 |
| Synthetic high-cardinality (this benchmark) | ~35.2 | ~3,100 |

**Takeaway: in real Chinese usage, a 128 KiB bitmap needs growing somewhere around
20,000–27,000 memories** (longer messages yield more tokens) — not the million the name
suggests. Monitor `gaze().veil_fill_ratio` in production.

### Latency and footprint

| Operation | p50 | p95 | mean |
|---|---|---|---|
| `imprint()` | 0.385 ms | 0.624 ms | 0.424 ms |
| `sense()` | 0.061 ms | 0.091 ms | 0.066 ms |
| `hunt()` | 0.097 ms | 0.129 ms | 0.101 ms |

_Measured at 10,000 memories, 2000 reps each._

| Item | Value |
|---|---|
| Memories | 10,000 |
| `veil.bin` | 131,093 B |
| `mist.db` | 64,860,160 B |
| Total disk | 69,411,853 B (6941.2 B/memory) |
| RSS after construct | 0 KB |
| RSS after fill | 32 KB |

**On disk usage**: `mist.db` size is driven by **unique token count**, not memory count.
Each unique token needs a row (80-char snippet + reference list). The benchmark uses
high-cardinality synthetic text (35.2 unique tokens/memory), so 10k memories yield
352k tokens → 65 MB. Real Chinese chit-chat has far lower cardinality (LCCC: ~4.1
unique tokens/utterance), giving roughly 8 MB for the same memory count.
If disk is a hard constraint, lower `MAX_REFS` (default **20**; setting it to 10 saves roughly another 25%) or shorten snippets.

### The crucial question: does it actually recover anything?

Three classes of "familiar but unsearchable" scenarios, compared against raw FTS5:

| Target | Scenario | FTS5 | sense() | hunt() | Rescued? |
|---|---|---|---|---|---|
| t1 | 实体改写+同义 | miss | unfamiliar | none | no |
| t2 | 实体改写+同义 | miss | familiar | none | yes |
| t3 | 同义表述 | miss | familiar | none | yes |
| t4 | 同义表述 | miss | familiar | none | yes |

| Metric | Result |
|---|---|
| FTS5 found the target | 0/4 |
| `sense()` flagged familiar | 3/4 |
| `hunt()` found the target | 0/4 |
| **Misses rescued by Déjà Vu** | **3/4** |
| Noise queries falsely familiar | 0/3 |

### English: common words are not evidence (v7.16.1)

English chat is full of *the / what / about / you / talked*. Up to v7.16.0 they counted as tokens, so after a single
English memory, any unrelated English question was "familiar" (v7.16.0 called all 6 unrelated questions below
familiar). They no longer count; what is left is content words. Same benchmark, section E2:

| Query | Should be | `sense()` | Score | Seen |
|---|---|---|---|---|
| the Sichuan restaurant we talked about | familiar | familiar | 0.50 | sichuan |
| when do I have my lessons with Alvarez | familiar | familiar | 1.00 | alvarez, lessons |
| did the skylight ever get fixed | familiar | familiar | 0.50 | skylight |
| what did we say about Byzantine gold coin minting | unfamiliar | unfamiliar | 0.00 | — |
| do you remember the penguin migration routes in Antarctica | unfamiliar | unfamiliar | 0.00 | — |
| that quantum computing paper we talked about | unfamiliar | unfamiliar | 0.00 | — |
| the ramen bar in Osaka I told you about | unfamiliar | unfamiliar | 0.00 | — |
| what was the name of my accordion teacher | unfamiliar | unfamiliar | 0.00 | — |
| how is the lighthouse renovation going | unfamiliar | unfamiliar | 0.00 | — |

Related questions flagged familiar: **3/3** (target found by `hunt()`: 3/3); unrelated questions falsely familiar: **0/6**. Background: 1,500 English memories.

The background above is synthetic and shares no content words with the unrelated questions, so it isolates the
common-word problem. On **real** English conversation (`benchmarks/dejavu_english_real.py`, LongMemEval-S user turns,
10 unrelated questions):

| Turns remembered | v7.16.0 | v7.16.1 |
|---|---|---|
| 20 | 10/10 familiar | **0/10** |
| 200 | 10/10 | **3/10** (bar, grandmother, teacher) |
| 2,000 | 10/10 | 10/10 (antarctica, podcast, workshop, …) |

With enough history, ordinary content words have all been seen — see "What it is not", point 6.

### Chinese: function words and cross-word fragments (v7.16.2)

Chinese is cut into fixed-boundary two-character pieces, and half of the pieces in a sentence are function words or
fragments across a word boundary: "我们聊过的那家川菜馆" (*the Sichuan restaurant we talked about*) gave
我们 / 们聊 / 聊过 / 过的 / 的那 / 那家 / 家川 / 川菜 / 菜馆, and only the last three are about the restaurant.
Since v7.16.2 a piece does not count if it contains a particle, 我 / 你 / 们, 这 / 那, 是 or 在 (these almost
never belong to words worth remembering, but are in most fragments), or if it is in a list of common words, time
deixis (上次, 之前, 最近) and recall phrasing (记得, 聊过, 提过). 他 / 她 are not on the character list, so 吉他
(*guitar*) and 其他 survive; common forms such as 他们 / 他说 are on the word list. The same sentence now gives
家川 / 川菜 / 菜馆.

On real Chinese chit-chat (`benchmarks/dejavu_chinese_real.py`, LCCC, 10 unrelated questions):

| Utterances remembered | v7.16.1 | v7.16.2 |
|---|---|---|
| 20 | 6/10 familiar (一下, 什么, 我说, 的那…) | **0/10** |
| 200 | 10/10 | **1/10** (工作) |
| 2,000 | 10/10 | **3/10** (企鹅, 工作, 玻璃, 老师) |

The Chinese results of section E above are unchanged (3/4 misses rescued, 0/3 noise).

---

## What it is *not*

Stated plainly, to prevent misuse:

1. **Not a retriever replacement.** Déjà Vu returns a *familiarity signal*, not memory content. Actual retrieval is still FTS / vector search's job.
2. **Does not reduce miss rate.** It doesn't find more — it splits "not found" into two classes.
3. **`hunt()` recovery is limited.** In our tests, pure paraphrase ("川菜馆" → "麻辣的店") and cross-language queries recover poorly, because recovery relies on literal token overlap and those cases have none. Semantic recovery needs an embedding model, which conflicts with the zero-dependency contract.
4. **Pure digits are not indexed.** The tokenizer captures English words and CJK n-grams only — `42` or `19289` won't be indexed. Extend the tokenizer if order numbers or amounts matter to you.
5. **False positives grow with scale.** See the knee table — this needs monitoring and growth, not install-and-forget.
6. **"Familiar" can rest on one ordinary word.** `familiar` is true as soon as any content token has been seen. With a large history (thousands of English turns), words like *teacher* or *workshop* have all been seen, so an unrelated question about "my accordion teacher" is familiar because of *teacher*. Look at `phantoms`: a rare name (*Alvarez*, *Sichuan*) is strong evidence, a generic noun is weak. Common words are not tokens at all: English (*the, about, talked*) since v7.16.1, Chinese (我们, 的那, 聊过) since v7.16.2.

One sentence: **it splits miss into two classes. That's all. But that cell was empty.**

---

## 30-line quickstart

```python
from nexsandglass.dejavu import DejaVu      # zero deps: stdlib + sqlite3

dv = DejaVu("./my_memory")                  # explicit dir, no config

# imprint: key is any reference you own (message id / line / URL)
dv.imprint("msg-1", "上周三和张老板聊了川菜馆的事，他说要换厨师")
dv.imprint("msg-2", "今天下午三点开季度复盘会")

# when retrieval comes back empty, first ask: have I seen this?
r = dv.sense("我们聊过的那家川菜馆")
print(f"familiarity {r.score:.2f} | {r.reason}")
# -> familiarity 0.67 | 2/3 tokens seen

# if familiar, pull the traces
if r.familiar:
    for p in dv.hunt("川菜馆 张老板"):
        print(f"  · {p.token} seen {p.sightings}x — 「{p.snippet}」")

dv.close()
```

Runnable version: [`examples/dejavu_minimal.py`](../examples/dejavu_minimal.py)

---

## Trade-off summary

| Decision | Choice | Cost |
|---|---|---|
| Model "seen or not" separately | Dedicated layer | One extra query (only on Veil hit) |
| Bloom vs exact set | Bloom | False positives (cost = one extra query, see §Design) |
| Bitmap size | Fixed 128 KiB | Needs growth after ~18k Chinese memories |
| Tokenization | CJK n-grams + English words (minus common words) | No pure digits; no synonym merging |
| Dependencies | Zero (stdlib + sqlite3) | No embedding model for semantic matching |
| Recovery | Literal overlap | Poor on paraphrase / cross-language |

These aren't a defect list — they're **deliberate boundaries**, each with a
measurable cost.
