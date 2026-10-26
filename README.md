# Nyx · nyx-memory

**English** | [简体中文](https://github.com/sixgodgit/nyx/blob/main/README.zh-CN.md)

> **Memory that stays with your agent for life** — single-user, local, no LLM on the write path.
> It remembers, finds, can be trusted, and can forget.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python) ![License](https://img.shields.io/badge/License-MIT-green) ![Version](https://img.shields.io/badge/version-7.16.0-blue) ![Deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen)

Nyx is a long-term memory layer for agents. It plugs into [Hermes](https://github.com/NousResearch/hermes-agent)
(as a memory provider), into Claude Desktop / Claude Code or any other MCP client, or into your own Python program.
Every message is kept verbatim; on demand Nyx brings back what was said, with dates, and what is believed *now*.
All data is plain files on your machine.

## Why Nyx

| | How | Measured |
|---|---|---|
| **Finds things** | Full-text, inverted index, TF-IDF, entities and (optional) semantic search, fused; questions like "how many times / how long between" get a few more results | LongMemEval-S (470 scored questions): the session holding the answer is in the top 5 **98.7%** of the time; LoCoMo evidence recall 0.575 (BM25 0.498). About 30–140 ms per recall |
| **Keeps time straight** | Bi-temporal facts: "when it was true" and "when it was learned" are stored separately | "I switched to a Geely back in February" is recorded as February, not as the day it was said; answers "what is believed now" and "what did I think in March" |
| **Can be trusted** | Source and trust are bound at write time: only what the user said themselves can become a rule; hearsay and web content come back labelled "unverified" | Poisoning drill: 0 of 16 attacks reach the agent's prompt unlabelled; 0 of the user's 8 normal messages are hurt |
| **Forgets, and can undo it** | Forgetting goes to a quarantine first (recall stops seeing it at once) and can be restored as-is during the retention period (30 days by default); purging after that really deletes, and checks that vectors, indexes and the graph are clean | 150 wrongly deleted memories restored byte for byte; a purge cannot be undone |
| **Cheap and private** | Zero LLM calls and zero API keys on the write path, zero runtime dependencies; no network by default ([exceptions](#privacy)) | Everything lives in `~/.neurobase` |
| **Secrets stay out of memory** | Tokens, API keys, private keys and passwords pasted into chat are replaced with `[hidden: HF token …a1b2]` before they are written anywhere | Across ~200,000 real turns in LongMemEval-S only 19 turns (0.01%) triggered it, all pasted code or config; 0 false hits on ordinary sentences |
| **Breakage is visible** | `python3 -m nexsandglass.doctor` checks every part in one command; in Hermes, a failed self-check makes the agent tell you | Semantic index, full-text index, forgetting integrity, rule poisoning… each ✅ / ❌ with the reason |

> The retrieval numbers above are **retrieval** metrics (no LLM, reproducible), not the "LLM answers + LLM judge"
> QA accuracy that Mem0 / Zep and others report; they cannot be compared directly. Methods and all numbers: [Benchmark](#benchmark).

---

## 🚀 Quick start

### Hermes (recommended)

```bash
hermes plugins install sixgodgit/nyx/hermes-plugin --enable
hermes config set memory.provider nyx
```

Restart Hermes (gateway: `hermes gateway restart`) and you are done. Only the `hermes` command is needed — nothing has
to be installed in the system Python first: Hermes puts the plugin in `~/.hermes/plugins/nyx/` and installs its declared
dependency, nyx-memory, into **its own** environment (re-applied after `hermes update`).
For semantic search (paraphrases and cross-language matches; downloads a ~470 MB model on first run), install
`sixgodgit/nyx/hermes-plugin-vector` instead.
[Secret vault](https://github.com/sixgodgit/nyx/blob/main/docs/secret-vault.md): also run `hermes plugins enable nyx-reveal`
and set `vault` to `on` in `hermes memory setup`.

<details>
<summary>Older Hermes (no <code>--no-deps</code> in <code>hermes plugins install --help</code>, e.g. hermes-agent 0.19.x from PyPI)</summary>

Older versions do not install the dependencies a plugin declares and do not read pip entry points. Install nyx-memory
into the Python Hermes runs on, then write the entry file:

```bash
pip install nyx-memory                       # the same Python as Hermes
python3 -m nexsandglass_hermes install       # also works on newer Hermes: there it runs the two commands above for you
```

`install` finds the Python Hermes uses from the `hermes` command (including `#!/bin/sh` wrapper scripts), installs
nyx-memory there if it is missing, writes the entry file in `plugins/nyx/`, sets `memory.provider: nyx`, and finally
loads it once with Hermes's own loader to confirm. `--check` only checks.
</details>

From then on every turn is remembered and recalled automatically. The agent also gets tools such as `nyx_belief`
(what is believed now / what was believed then), `nyx_forget` / `nyx_restore` (forget and restore) and `nyx_health`
(self-check); in the terminal there are `hermes nyx doctor`, `hermes nyx vault …` and more. Full guide:
[`nexsandglass_hermes/README.md`](https://github.com/sixgodgit/nyx/blob/main/nexsandglass_hermes/README.md).

### MCP (Claude Desktop / Claude Code / any MCP client)

```bash
pip install nyx-memory
claude mcp add nyx -- python3 -m nexsandglass.interfaces.sandglass_mcp     # Claude Code
```

For other clients, add this to the config file:

```json
{ "mcpServers": { "nyx": { "command": "python3", "args": ["-m", "nexsandglass.interfaces.sandglass_mcp"] } } }
```

26 tools; see [MCP tools](#mcp-tools).

### Python

```python
from nexsandglass.runtime import observe, recall, forget, restore

observe("My cat is called Miso and she hates the vacuum cleaner", source="user")   # source="user" = said by the user
print(recall("what is my cat called").text)    # → My cat is called Miso and she hates the vacuum cleaner

r = forget({"contains": "Miso", "dry_run": True})   # preview first
r = forget({"contains": "Miso"})                    # into the quarantine: recall stops seeing it at once
print(r["recoverable"], r["purge_after"])           # True 2026-11-25 …
restore(r["preview"][0]["mem_id"])                  # restored as-is during the retention period
```

Without `[vector]`, recall is lexical: "which editor do I use" will not find "I switched from VS Code to Zed"
because they share no words. Install `nyx-memory[vector]` for paraphrases. Fact extraction (current beliefs such as
"user lives in Shanghai (since 2026-09-01)") is rule-based and mostly covers Chinese today; see [Known limitations](#known-limitations).

After installing, run `python3 -m nexsandglass.doctor`; all ✅ means everything works.

---

## ⚙️ How it works

**Write** (after every turn, no LLM call)

1. The message is appended to `sandglass.txt` and gets a unique `mem_id` — this journal is the single source of truth; everything else can be rebuilt from it
2. Source and trust are bound: the user themselves / a bot / someone else in a group / external content, fixed at write time and not changeable by editing the text later
3. Promotion gate: what is worth keeping goes to the processing layer (facts, graph, engram); small talk and verification codes are not promoted (the message is still in the journal and still searchable)
4. Understanding: "switched to a Geely on Feb 25" → (user, car, Geely, from Feb 25, still true), stored as a bi-temporal fact; the old fact is not overwritten but gets an end date
5. Full-text, inverted and (optional) semantic indexes are updated incrementally

**Recall** (before every turn, entirely local)

1. Full-text (FTS5), inverted index, TF-IDF, entities and (optional) semantic vectors are searched
2. Fused ranking: complete literal matches first, semantics as support (half weight, only hits clearly above background count)
3. Current beliefs are added ("user lives in Shanghai (since 2026-09-01)")
4. Trust gate: memories from a tainted source stay out; unverified ones are labelled and cannot act as rules
5. Injected into the agent with dates: 12 entries by default; 20 for "how many times / how long / which came first / before or after" questions

---

## 📋 Features

| Feature | Module | What it does |
|---|---|---|
| 🆔 **Journal + ID hub** | `core/memid.py`, `core/sandglass_log.py` | Every memory has a `mem_id` and a line span; tombstones keep deleted memories from coming back on rebuild |
| 🔍 **Multi-route search** | `core/search_router.py`, `core/semantic.py` | FTS5 / inverted index / TF-IDF / shadow sand + optional semantics (local multilingual model or an OpenAI-compatible API), RRF fusion |
| 🕰️ **Bi-temporal facts** | `engram/loops/temporal_fact.py`, `core/understand.py` | `as_of` asks about the world, `known_at` about what was believed then, `belief_timeline` about how a view changed; hypothetical times never pose as stated ones |
| 🛡️ **Source trust** | `core/provenance.py` | trusted / unverified / tainted; only what the user said can become a rule (against OWASP ASI06 memory poisoning) |
| 🕯️ **Forgetting quarantine** | `core/quarantine.py`, `core/erasure.py` | Two-stage forgetting: quarantine → restore, or erase when due; erasure goes through the journal, indexes, graph and vectors and can be verified independently |
| 🔑 **Secrets stay out** | `core/secret_filter.py`, `scrub.py` | Tokens / API keys / private keys / connection strings / passwords are hidden before writing; `scrub` hides already-stored ones in place and re-checks the raw bytes |
| 🔐 **Secret vault** (optional) | `core/secret_vault.py`, `vault.py` | The hidden values are stored encrypted and handed back to the user after a human confirmation; with direct reveal the model only gets a handle, never the value (tested on real Hermes) |
| 🩺 **Self-check** | `doctor.py` | Hub vs journal consistency, each index, forgetting integrity, quarantine due dates, rule poisoning — each with the reason |
| 🧯 **Check and repair** | `repair.py`, `core/forensics.py` | When the hub does not match the journal, checks record by record by content: adds what is missing, moves misplaced rows back, recognises overwritten memories, removes synthetic records and the "facts" derived from them; backs up first, never changes the journal |
| 🔌 **Hermes memory provider** | `nexsandglass_hermes/`, `core/memory_provider.py` | Implements Hermes's `MemoryProvider` interface; installed with `hermes plugins install sixgodgit/nyx/hermes-plugin --enable` |
| 🔌 **MCP server** | `interfaces/sandglass_mcp.py` | stdio, standard library only, 26 tools |
| 👻 **Déjà Vu** | `dejavu/` (standalone subpackage) | Tells "never talked about it" from "talked about it but cannot find it"; see the next section |
| 🕸️ **Knowledge graph** | `features/weavethread.py` | Entity–relation triples + typed relations (belongs_to / evolved_from / contradicts …) + PPR graph boost |

<details>
<summary><b>More modules</b> (memory processing, dreaming, persona, visualisation…)</summary>

| Feature | Module | What it does |
|---|---|---|
| 🧬 Memory processing | `engram/` | Tulving's four memory types + Ebbinghaus decay; semantic overwrites / emotional reinforces / procedural dedups / episodic inserts |
| 🌱 Candidate promotion | `runtime/promotion.py` | "What is worth remembering": Observation → Extract → Score → Type → Promote / Session / Drop |
| 🧭 Intent-aware recall | `runtime/intent.py` | MemoryIntent: ranking aware of entities / time / domain / relations |
| 📦 Memory bundle | `runtime/bundle.py` | System-prompt assembly from 10 slots, trading relevance against token cost |
| 🌙 Dreaming and consolidation | `engram/dream_pipeline.py`, `runtime/consolidation.py` | Light-sleep summaries → deep-sleep internalisation → inspiration links; every change starts as a proposal, is validated, and can be rolled back from a snapshot |
| 🔄 Self-evolving memory | `engram/evolve.py`, `engram/loops/` | Four loops: facts↔graph, dreams↔processing, persona↔context, recall↔importance |
| 📊 Emotion and persona | `core/emotion_vocab.py`, `l3/` | State tracking, decision drift, echo, open tasks |
| 🧩 Skill distillation | `features/skill_distiller.py` | Recurring workflows → reusable skill drafts |
| 🤖 LLM graph extraction | `core/llm_extract.py` | Optional LLM-based graph extraction and entity normalisation (falls back to rules on failure) |
| 📚 NyxBook | `nyx-web/`, `nyx_server.py` | Browse memories, write a diary, stamp entries (FastAPI + Vue 3, optional) |

The development history (design and trade-offs of phases 0–7) is in
[`CHANGELOG.md`](https://github.com/sixgodgit/nyx/blob/main/CHANGELOG.md) and [`docs/`](https://github.com/sixgodgit/nyx/tree/main/docs) (mostly in Chinese).
</details>

---

## 👻 Déjà Vu: "never talked about it" vs "talked about it but cannot find it"

The user says "that Sichuan restaurant we talked about" and search comes back empty — did it never come up, or was it
phrased differently back then? Mainstream retrievers return the same thing in both cases: nothing.

| | Vector search | BM25 / FTS | **Nyx Déjà Vu** |
|---|---|---|---|
| Never happened | non-empty top-k, all noise | 0 hits | **unfamiliar** (a clear signal) |
| Happened but not found | non-empty top-k, all noise | 0 hits | **familiar + traces found** |

```python
from nexsandglass.dejavu import DejaVu      # no dependencies: stdlib + sqlite3

dv = DejaVu("./my_memory")
dv.imprint("msg-1", "Last Wednesday I told Sam about that Sichuan place on Clement Street")
dv.sense("a ramen bar in Osaka")       # -> familiar=False, score 0.0 ("none of 3 tokens seen")
dv.sense("the Sichuan place")          # -> familiar=True, score 0.67 ("2/3 tokens seen")
dv.hunt("Sichuan")                     # -> [Phantom(token='sichuan', refs=['msg-1'], ...)]
```

Two layers: **Veil** (a 128 KiB Bloom filter) answers "has this word been seen", **Mist** (SQLite) records when,
where and how often it appeared. To be clear: it does not raise recall; it only splits misses into two kinds —
**good at classifying, limited at retrieving**. English tokenisation is still coarse: common words such as "about" and
"that" count as seen too, so short English questions can look familiar. Measurements:
[`benchmarks/dejavu_bench.py`](https://github.com/sixgodgit/nyx/blob/main/benchmarks/dejavu_bench.py); design:
[`docs/dejavu.md`](https://github.com/sixgodgit/nyx/blob/main/docs/dejavu.md).

---

## 📦 Installation (details)

| Method | Command | Notes |
|---|---|---|
| pip | `pip install nyx-memory` | Zero runtime dependencies; includes the Hermes provider and the MCP server |
| pip + semantic search | `pip install "nyx-memory[vector]"` | Installs sentence-transformers; the first run downloads the model (~470 MB) to `~/.cache/nexsandglass/models` |
| From source | `./install.sh [--vector]` (Windows: `install.bat [--vector]`) | Checks the Python version, installs, and points out plugin files left by old versions |
| Docker | `docker build -t nyx-memory .` | The MCP server speaks stdio: configure `docker run -i --rm -v nyx-data:/data nyx-memory` in your MCP client; for semantic search add `--build-arg EXTRAS="[vector]"` |

Package name `nyx-memory` (PyPI), Python module `nexsandglass`. Requires Python 3.10+.

**Upgrading**: `pip install -U nyx-memory` (with semantic search: `"nyx-memory[vector]"`), restart Hermes, run `doctor` once.
- On first start, vectors for all existing memories are filled in the background (a few thousand take tens of minutes on a local CPU model); semantic recall becomes complete gradually and `doctor` shows "backfilling"
- Machines set up with the v3.x `install.sh`: delete `~/.hermes/plugins/memory/nexsandglass` and `~/.hermes/plugins/sandglass`.
  Those are old copied plugins; left in place they run alongside the current one and the same message may be remembered twice

---

## 🔧 Configuration

**Self-check**: `python3 -m nexsandglass.doctor` (in Hermes: `hermes nyx doctor`; `--json` for machines; exit code 1 when
something fails). Run it after upgrading, or whenever memory "seems wrong".

**Hermes users**: `hermes memory setup` writes `$HERMES_HOME/nyx.json` (`data_dir`, `prefetch_tokens`, `lang`, semantic
search `semantic` / `embedding_model` / `embedding_api_url` / `embedding_api_key`; secrets go only to `.env`). Keys:
[`nexsandglass_hermes/README.md`](https://github.com/sixgodgit/nyx/blob/main/nexsandglass_hermes/README.md).

**Language**: output for people (terminal commands, `hermes memory setup`, the installer) is English by default and
Chinese with `NYX_LANG=zh` (Hermes: `lang: zh`); with `auto`, a Chinese system locale picks Chinese. Text the model reads
(tool descriptions, recall labels, placeholders) is always English. Stored memories are never rewritten.

**Environment variables** (all entry points; under Hermes, environment variables win over `nyx.json`):

| Variable | Default | Effect |
|---|---|---|
| `NEXSANDBASE_HOME` | `~/.neurobase` | Memory data directory |
| `NYX_LANG` | `auto` | Language of human-facing output: `en` / `zh` / `auto` (Chinese locale → Chinese). Hermes: `lang` |
| `NYX_EMBED` | `auto` | Semantic backend: `auto` / `local` / `api` / `off` |
| `NYX_EMBED_MODEL` | local `paraphrase-multilingual-MiniLM-L12-v2`; API `text-embedding-3-small` | Embedding model |
| `EMBEDDING_API_URL` / `EMBEDDING_API_KEY` | empty | OpenAI-compatible `/embeddings` |
| `NYX_EMBED_Z` | `3` | How far above background (robust z-score) a semantic hit must be to count as evidence |
| `NYX_EMBED_WEIGHT` | `0.5` | Weight of the semantic vote in fusion (the lexical vote is 1) |
| `NYX_EMBED_CACHE` | `~/.cache/nexsandglass/models` | Where local embedding models are stored |
| `NYX_RERANK` | `off` | `local` = rerank recall with a local cross-encoder (needs `[vector]`). More accurate, slower, worse on preference questions; see [Benchmark](#benchmark). `NYX_RERANK_MODEL` / `_DEPTH` (50) / `_MODE` (rrf) / `_MAXLEN` (128) / `_BUDGET_MS` (2000) are tunable |
| `NYX_SECRET_FILTER` | `on` | Hide secrets / passwords before writing: `on` (tokens + passwords + `<private>`) / `tokens` (leave passwords) / `off` |
| `NYX_L3_LLM` | off | `1` = L3 analysis (persona / decision drift / phantom decisions) calls DeepSeek / OpenRouter (`DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`). Before v7.15.2 it did so whenever a key was present |
| `NYX_VAULT` | `off` | `on` = hidden values are stored encrypted in the vault (needs `pip install 'nyx-memory[vault]'`). `NYX_VAULT_REVEAL` (`direct` / `cli` / `llm`), `NYX_VAULT_KINDS` (`all` / `tokens`), `NYX_VAULT_BACKEND` (`auto` / `file` / `keyring`), `NYX_VAULT_KEY`; see [docs/secret-vault.md](https://github.com/sixgodgit/nyx/blob/main/docs/secret-vault.md) |
| `NYX_BREADTH_LINES` | `20` | Entries recalled and injected for "how many times / how long / which came first / before or after" questions; `0` = no widening |
| `NYX_UNDERSTAND_LLM` | off | `1` = fact extraction via an LLM (results must pass validation; falls back to rules) |
| `WTHREAD_LLM_EXTRACTION` | off | Extra knowledge-graph extraction via an LLM (needs `LLM_EXTRACT_API_URL`) |
| `LLM_EXTRACT_API_URL` / `NYX_UNDERSTAND_MODEL` | empty / `deepseek-v4-flash` | OpenAI-compatible gateway and model for extraction |
| `NYX_FORGET_RETENTION_DAYS` | `30` | Quarantine retention period |
| `NYX_BUNDLE_MAX_TOKENS` | `1500` | System-prompt injection budget |
| `NYX_JOURNAL_MIRROR_MB` | `32` | Size limit (journal file size, MB) for the in-memory journal mirror; above it, falls back to line scans. Uses about 3.5× the journal size in memory |
| `NYX_WARM_WAIT_SECONDS` | `10` | How long Hermes's first turn waits for background warm-up |
| `NYX_RUNTIME` | `1` | `0` = emergency rollback to the old write path |
| `NYX_EMBED_PROVIDER` | empty | Custom embedding backend (`package.module:factory`) |
| `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY` | empty | Used only with `NYX_L3_LLM=1`: L3 analysis (persona / decision drift / phantom decisions) calls DeepSeek / OpenRouter; see [Data and privacy](#privacy) |

---

<a id="mcp-tools"></a>

## 🛠️ MCP tools

Start (stdio): `python3 -m nexsandglass.interfaces.sandglass_mcp`

| Tool | What it does |
|------|------|
| `memory_observe` | Unified write (through the promotion gate and source binding) |
| `memory_recall` | Unified recall (intent-adaptive + trust gate) |
| `memory_feedback` | Recall feedback (reinforce / weaken) |
| `memory_forget` | Forget: `memory_id` / `contains` / `dry_run`; **goes to the quarantine by default** and can be restored during retention; returns `ok=false` when nothing was removed |
| `sandglass_search` / `sandglass_semantic` | Keyword search / synonym + SimHash + TF-IDF search |
| `sandglass_recent` / `sandglass_ping` | Last N entries / health check (`status=degraded` when the semantic index is broken) |
| `sandglass_persona` / `sandglass_tasks` | User persona / open tasks |
| `sandglass_offset` / `sandglass_echo` / `sandglass_chart` | Decision drift / sentiment / emotion-entropy chart |
| `sandglass_dejavu` | Déjà Vu familiarity |
| `sandglass_thread` / `sandglass_thread_graph` / `sandglass_thread_weave` / `sandglass_thread_add` | Knowledge graph query / subgraph / causal chain / add a triple by hand |
| `sandglass_dream` | Phantom decisions ("what if I had chosen the other one") |
| `sandglass_export` / `sandglass_import` / `sandglass_migrate` | Export / import (including ChatGPT and Claude exports) / pack for migration |
| `sandglass_soul_export` / `sandglass_soul_merge` | Soul-diff export / merge |
| `fact_store` / `fact_feedback` | Fact store / trust feedback |

> MCP has no restore tool yet; restore forgotten memories with `scripts/nyx_quarantine.py restore <mem_id>` or Python's `restore()`.
> Bi-temporal queries (now / believed then / how it changed) and the quarantine tools are only in the Hermes provider for now (`nyx_belief` / `nyx_restore` / `nyx_quarantine`).

---

<a id="privacy"></a>

## 🔒 Data and privacy

Default directory `~/.neurobase` (change with `NEXSANDBASE_HOME`). **`sandglass.txt` is the single source of truth**; everything else can be rebuilt from it.

| File | Contents |
|------|------|
| `sandglass.txt` | Raw journal: `time \| sender \| text`, append-only |
| `nyx.db` | ID hub (mem_id ↔ line), tombstones, source and trust, forgetting quarantine |
| `sandglass.db` / `sandglass.idx` | FTS5 full-text index / inverted index |
| `shadow_sand.db` | Shadow sand (trust scores, entities, tags) + bi-temporal facts `wthread_triples` |
| `semantic.db` | Semantic index (table `embeddings`; since v7.13.3, not created without an embedding backend). The old `vectors.db` is no longer used |
| `engram_store.jsonl` | Engram processing layer (four memory types + decay) |
| `veil.bin` / `mist.db` | Déjà Vu (Bloom filter / sightings) |
| `persona/`, `archive/` | Persona and decision log / cold archive |

Hermes users: when the data directory is outside `HERMES_HOME`, `hermes backup` includes it automatically (the provider's `backup_paths`).

**Secrets and passwords stay out of memory** (v7.14.0): tokens pasted into chat (Hugging Face, GitHub, OpenAI / DeepSeek /
Anthropic / OpenRouter, PyPI, AWS, Google, Slack, Stripe, Telegram, JWT…), private keys, connection strings with passwords,
assignments like `XXX_API_KEY=…`, "my password is …", and anything wrapped in `<private>…</private>` are replaced with
a placeholder **before they are written anywhere**: tokens keep their last 4 characters (`[hidden: HF token …a1b2]`, so the
agent can still say "you gave me an HF token"), passwords keep nothing. The journal, hub, indexes, shadow sand, graph and
vectors only ever see the placeholder; queries sent to an embedding API are hidden first too. `NYX_SECRET_FILTER=tokens`
covers tokens only, `off` turns it off. Placeholders stored before v7.16.0 read `[已隐去: …]`; they are still recognised
and never rewritten. Hermes's own session logs (under `~/.hermes`) are not Nyx's to manage.

**Want the agent to be able to give a key back later?** (v7.15.0, off by default) Turn on the secret vault: `NYX_VAULT=on`
(in Hermes: `vault: on`) and `pip install 'nyx-memory[vault]'`. The hidden values are stored encrypted (the master key is in
`~/.config/nyx/vault.key`, outside the data directory) and placeholders get an id (`[hidden: HF token …a1b2 #v3]`).
When you say "send me the HF token I gave you", the agent calls `nyx_secret_reveal`: a human confirms first (Hermes's
approval; when approvals are off via `/yolo`, you reply with a confirmation phrase instead), then the companion plugin
swaps the handle for the value just before the reply goes out — **the model provider never sees the value**, and Hermes's
session log only has the handle (enable once with `hermes plugins enable nyx-reveal`). Safest of all is the terminal:
`python3 -m nexsandglass.vault show v3`. The moment a key is pasted into chat it has already been sent to the model
provider, so a pasted key should still be rotated; and if the bot is in a group, anyone in the group counts as the user.
Threat model and tests: [docs/secret-vault.md](https://github.com/sixgodgit/nyx/blob/main/docs/secret-vault.md).

**Deleted means gone**: after a quarantine purge (or `mode="purge"`), Nyx merges the full-text index and VACUUMs every
database — after SQLite deletes a row the text stays in the file's free pages, and tokens deleted from FTS5 survive even a
VACUUM (that was the case before v7.14.0).

**When it goes online**: with nothing configured, Nyx makes no outbound requests. The exceptions, with what gets sent in brackets:
- First run after installing `[vector]`: the embedding model is downloaded from Hugging Face (none of your data is sent)
- `EMBEDDING_API_URL` set: semantic search uses the embedding API you chose (memory text and queries)
- `NYX_UNDERSTAND_LLM=1`, or `WTHREAD_LLM_EXTRACTION` with `LLM_EXTRACT_API_URL`: fact / graph extraction uses the LLM you chose (the sentence being extracted)
- `NYX_L3_LLM=1` (Hermes: `l3_llm: on`) with `DEEPSEEK_API_KEY` or `OPENROUTER_API_KEY`: persona, decision drift and phantom
  decisions call DeepSeek / OpenRouter (relevant memory snippets, the persona, recent decisions). **Before v7.15.2 this
  did not need turning on — a key in the environment was enough.** In Hermes the main model usually goes through
  OpenRouter, so every system-prompt build sent the persona and recent decisions out. It is off by default now

---

## ❓ FAQ

**Semantic search not working?** Run `doctor` and look at `semantic_index`. Common causes: `[vector]` not installed; the model
is still downloading (first run); Hugging Face's download servers are unreachable (on restricted networks try
`HF_HUB_DISABLE_XET=1`, or download on a connected machine, copy `~/.cache/nexsandglass/models` over and point
`NYX_EMBED_CACHE` at it). A broken semantic index does not affect lexical recall and is never silent: `doctor` shows ❌, and in Hermes the agent tells you.

**The same message remembered twice?** Most likely the v3.x plugin files are still there; see "Upgrading" above.

**Want to delete a memory?** In the agent use `nyx_forget` (preview, then confirm; goes to the quarantine); in Python `forget()`.
Changed your mind: `nyx_restore` / `restore()` or `python3 scripts/nyx_quarantine.py restore <mem_id>`.
After the retention period (30 days by default) nothing is deleted **automatically**: run
`python3 -c "from nexsandglass.core import erasure; print(erasure.purge(apply=True))"` to really delete
(in a source checkout also `python3 scripts/nyx_quarantine.py purge --apply`); a daily cron is a good idea, and `doctor` shows ❌ when purges are overdue.

**Want more accurate recall?** With `[vector]` installed you can set `NYX_RERANK=local` (Hermes: `rerank: local`):
+4.7 points of LoCoMo evidence recall, at the cost of 0.3–1.5 s more per recall and worse preference questions ([Benchmark](#benchmark)).

**Too many memories injected, too many tokens?** In Hermes lower `prefetch_tokens` (default 600 ≈ 12 entries); `NYX_BREADTH_LINES=0` turns off the 10 extra entries for counting / time questions.

**Several agents / machines sharing one memory?** Point them at the same `NEXSANDBASE_HOME` (several processes on one machine
reading and writing at once is safe: writes take a cross-process lock). Across machines, use `sandglass_export` /
`sandglass_migrate`; do not sync a live data directory with a cloud drive.

**Pasted tokens / passwords into chat before?** Writes since v7.14.0 hide them automatically. For older ones run
`python3 -m nexsandglass.scrub` (Hermes: `hermes nyx scrub`) to see how many and where (read-only, never prints the values),
then stop Hermes / the MCP server and run it with `--apply` to hide them in place. It rebuilds the indexes, VACUUMs, and
finally scans the whole data directory byte by byte. Old repair backups (`.db` files under `backups/`) are not changed;
they are listed so you can delete them once you are sure. **Also revoke the token on its platform** — it has already been
sent to the LLM provider you use; Nyx can only delete its own copy.

**"⚠️ Nyx health: …" in the system prompt?** A subsystem is broken (for example the semantic index); recall still works but is incomplete. Run `doctor` for the reason.

**`hub_vs_journal` / `fts_index` / `erasure_integrity` red in `doctor`?** The hub no longer matches the journal (for example,
tests or another program wrote into the data directory). First run `python3 -m nexsandglass.repair`: a read-only check that
sorts records by content into missing, misplaced, overwritten, synthetic orphans and real vs false "leaks", reporting only
counts and seq ranges. Then stop the Hermes gateway / MCP server and run `python3 -m nexsandglass.repair --apply` (it backs up
to `<data dir>/backups/` first and does not change a single byte of the journal), and restart the services afterwards.
**Do not `forget` the seqs that `erasure_integrity` reports**: a tombstone may point at the wrong place, and you would erase
real memories. Records listed as "unfinished erasures" (journal text still there) are erased from the journal only with
`--finish-erasures`; if the journal also has senders that look like test data, add `--forget-senders golden,e2e` once you are
sure (they go to the quarantine and can be restored for 30 days).

---

<a id="known-limitations"></a>

## ⚠️ Known limitations

- **Single-user by design**: one memory per person; one process serves one data directory at a time
- **End-to-end QA accuracy not measured yet**: the [Benchmark](#benchmark) numbers are retrieval metrics; Mem0 / Zep and others report QA accuracy (an LLM answers and an LLM judges)
- **Weak spots**: on LoCoMo multi-hop questions the agent sees only 41% of the evidence (48% with reranking); LongMemEval preference questions 0.71 (lower with reranking)
- **Little English fact extraction**: rule-based extraction mostly covers Chinese; in English Nyx relies on retrieving the original text with its date. Without `[vector]`, English paraphrases that share no words with the memory are not found ("which editor do I use" does not find "I switched from VS Code to Zed"). Install `[vector]`, or enable `NYX_UNDERSTAND_LLM` for LLM extraction
- **Coarse English tokenisation in Déjà Vu**: common words count as seen, so short English questions can look familiar
- **Small default embedding model**: the local multilingual MiniLM is small and fast; with a stronger API model the fusion weights may need retuning (`NYX_EMBED_Z` / `NYX_EMBED_WEIGHT`)

---

<a id="benchmark"></a>

## 📊 Benchmark

Both public benchmarks measure **retrieval** only (no LLM, reproducible), feeding every turn through the Hermes provider's real write path.
Semantic search uses the default local model `paraphrase-multilingual-MiniLM-L12-v2` (`pip install "nyx-memory[vector]"`).

**LongMemEval-S** (`benchmarks/longmemeval_eval.py`, 500 questions, ~50 sessions / 490 turns each; session-level recall as
officially defined, the 30 abstention questions not scored; results in `benchmarks/results/longmemeval_s_retrieval.json`)

| Type | Questions | any in top 5 | all in top 5 | all in top 10 | answer turns the agent sees | lexical only, all in top 5 |
|---|---|---|---|---|---|---|
| Knowledge update | 72 | 1.000 | 1.000 | 1.000 | 0.944 | 0.889 |
| Multi-session | 121 | 0.992 | 0.793 | 0.901 | 0.804 | 0.603 |
| Single-session (assistant) | 56 | 1.000 | 1.000 | 1.000 | 0.982 | 0.982 |
| Single-session (preference) | 30 | 0.900 | 0.900 | 0.900 | 0.706 | 0.700 |
| Single-session (user) | 64 | 1.000 | 1.000 | 1.000 | 0.961 | 0.984 |
| Temporal reasoning | 127 | 0.984 | 0.779 | 0.866 | 0.804 | 0.693 |
| **Overall** | 470 | **0.987** | **0.881** | **0.932** | **0.862** | 0.774 |

"any in top 5" = at least one answer session among the top 5 recalled; "all in top 5" = when the answer is spread over
several sessions, the top 5 found all of them; "answer turns the agent sees" = of the turns the dataset marks as holding
the answer, how many are among the entries injected into the agent (12 by default, 20 for "how many / how long / which
first" questions) — the closest to whether the agent can answer (v7.13.7).

**LoCoMo** (`benchmarks/locomo_eval.py`, 10 long two-person conversations, 5,882 turns over months; 1,536 scored questions,
446 adversarial and 4 without evidence labels excluded as usual; the metric is how much of the labelled evidence is in the
top k; the reference is plain BM25 over the same sentences; results in `benchmarks/results/locomo_retrieval.json`)

| Category | Questions | nyx R@5 | lexical R@5 | BM25 R@5 | nyx R@10 | BM25 R@10 | evidence the agent sees |
|---|---|---|---|---|---|---|---|
| Multi-hop | 282 | **0.301** | 0.289 | 0.180 | **0.397** | 0.264 | 0.413 |
| Temporal | 321 | **0.674** | 0.666 | 0.611 | **0.744** | 0.678 | 0.756 |
| Open-domain | 92 | 0.273 | **0.282** | 0.243 | **0.352** | 0.272 | 0.358 |
| Single-hop | 841 | **0.661** | 0.615 | 0.590 | **0.725** | 0.655 | 0.732 |
| **Overall** | 1536 | **0.575** | 0.546 | 0.498 | **0.646** | 0.565 | 0.656 |

How to read these tables:
- This is **retrieval**, not the scores others report. The LongMemEval / LoCoMo numbers from Mem0, Zep and Hindsight are QA
  accuracy where an LLM reads the recalled results, answers, and another LLM judges; a different measure, so these tables
  cannot be compared with them. End-to-end QA has not been run yet (it needs an LLM API)
- The two benchmarks differ a lot: most LongMemEval sessions are filler unrelated to the question, which semantics separates
  easily; LoCoMo is the same two people talking from start to finish, every sentence alike, so names and keywords carry it.
  Nyx's fusion (v7.13.6) chose its parameters on one half of each benchmark and was validated on the other half; it is no
  worse than lexical-only on either (`NYX_EMBED_Z=2 NYX_EMBED_WEIGHT=1` restores the old fusion)
- "How many times / how long / which first / before or after" questions give the agent 10 more entries (v7.13.7):
  answer turns seen on LongMemEval multi-session 0.701 → 0.804, temporal reasoning 0.715 → 0.804 (`NYX_BREADTH_LINES=0` turns it off)
- Weak spots: on LoCoMo multi-hop all the evidence is in the top 10 only 18% of the time and the agent sees only 41%; LongMemEval preference 0.71
- Up to v7.13.4, nyx's LoCoMo R@5 was **0.046** (a ranking bug in recall aggregation; see the v7.13.5 changelog)

**Optional: cross-encoder reranking** (`NYX_RERANK=local`, v7.14.0, off by default). The router fetches 50 entries, the local
multilingual cross-encoder `mmarco-mMiniLMv2-L12` scores each pair (one vote, the original rank the other), and the original
number is kept. Parameters were chosen on half of each benchmark; the table shows the **other** (held-out) half:

| Held-out half | No rerank | Rerank |
|---|---|---|
| LoCoMo: evidence the agent sees | 0.681 | **0.728** |
| LoCoMo: R@5 | 0.610 | **0.674** |
| LongMemEval-S: answer turns the agent sees | 0.855 | **0.867** |
| LongMemEval-S: all in top 5 | 0.875 | **0.907** |
| LongMemEval-S: of which preference | **0.694** | 0.500 |

Cost: 0.3–1.5 s more per recall (CPU; short memories ~0.2 s on 4 threads, long ones ~1 s), and a ~470 MB model. Preference
questions ("recommend me a few…") get worse: the cross-encoder learned "which passage answers this question" and ranks
generic recommendations above preferences the user stated. Hence off by default.

```bash
pip install "nyx-memory[vector]"     # first run downloads models to ~/.cache/nexsandglass/models (change with NYX_EMBED_CACHE)
# LongMemEval (MIT): https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned
python3 benchmarks/longmemeval_eval.py longmemeval_s_cleaned.json --workers 8 --json benchmarks/results/longmemeval_s.json
python3 benchmarks/longmemeval_eval.py longmemeval_s_cleaned.json --workers 8 --no-semantic --json longmemeval_s_lexical.json
# LoCoMo (CC BY-NC 4.0, not in the repository): https://github.com/snap-research/locomo
python3 benchmarks/locomo_eval.py locomo10.json --workers 10 --json benchmarks/results/locomo.json
```

When the semantic backend is on but the index was not built, both harnesses fail loudly (v7.13.6) — lexical-only results are never recorded as "semantic on".

**Reproducible evaluations in the repository** (results in `benchmarks/results/`):

| Evaluation | What it measures | Version |
|---|---|---|
| `benchmarks/locomo_eval.py` | LoCoMo retrieval evidence recall (above), with a BM25 reference | v7.13.5 |
| `benchmarks/longitudinal_eval.py` | Half a year of one person's life: now / believed then / at some past moment, scored separately; includes held-out phrasings | v7.9–v7.11 |
| `benchmarks/poisoning_drill.py` | Memory poisoning: 0 of 16 attacks reach the prompt unlabelled, 0 of the user's 8 hurt | v7.8 |
| `benchmarks/forget_restore_drill.py` | 150 wrongly deleted → restored byte for byte; a purge is still irreversible | v7.6 |
| `tests/integration/hermes_real_check.py` | The full provider chain against real Hermes source (needs `HERMES_AGENT_SRC`) | v7.12 |
| `benchmarks/dejavu_bench.py` | Déjà Vu familiar / unfamiliar classification | v7.5 |
| `benchmarks/recall_latency.py` | Hermes provider start-up, first turn and per-turn recall time with 30K / 150K memories; conversation mode follows a real rhythm (write one, query one) | v7.13.2–v7.13.3 |

> Older versions listed a "lexical recall 75%" from `tests/eval/run_eval.py` here. That script scored an in-memory list with
> its own simple tokenizer, **never going through nyx's retrieval**, so it said nothing about nyx's recall; it was removed.

---

## 📝 Recent updates

- **v7.16.0**: English first — docs, terminal commands and `hermes memory setup` are English by default (`NYX_LANG=zh`, or `lang: zh` in Hermes, for Chinese; a Chinese system locale picks Chinese automatically); text the model reads (tool descriptions, recall labels, placeholders) is English throughout; stored data is not rewritten, and old Chinese placeholders and labels are still recognised
- **v7.15.4**: recall about twice as fast (10K real turns, prefetch p50 from ~88 ms to ~47 ms), results identical
- **v7.15.3**: newer Hermes recognises Nyx as a memory provider, so `hermes plugins install … --enable` sets `memory.provider`; post-install notes English first
- **v7.15.2**: L3 analysis calling DeepSeek / OpenRouter must now be turned on (`l3_llm: on`) — before, in Hermes it almost always sent the persona and recent decisions by default; new `hermes nyx …` terminal commands
- **v7.15.1**: install with Hermes's own plugin command: `hermes plugins install sixgodgit/nyx/hermes-plugin --enable` (the v7.15.0 install command failed on newer Hermes)
- **v7.15.0**: optional secret vault — keys pasted into chat can later be handed back by the agent (human confirmation; with direct reveal the model provider never sees the value, tested on real Hermes); Hermes installation fixed
- **v7.14.0**: secrets and passwords stay out of memory (hidden before writing; already-stored ones hidden in place by `scrub`); purged means gone from disk too (the full-text index used to keep it); optional cross-encoder reranking (+4.7 points LoCoMo evidence recall, off by default); end-to-end QA harness
- **v7.13.9**: tests can no longer write into a real data directory (session isolation + runtime guard); new `python3 -m nexsandglass.repair` — repairs a polluted hub by content without losing a real memory, reviving a forgotten one, or keeping synthetic "facts" and their derived data
- **v7.13.8**: README rewritten (quick start, how it works, privacy, FAQ, known limitations); `doctor` no longer shows false ❌ on a fresh install; install script and Docker fixed (the old script installed a gateway plugin that recorded every message twice)

All versions, with measurements and trade-offs: [`CHANGELOG.md`](https://github.com/sixgodgit/nyx/blob/main/CHANGELOG.md) (in Chinese up to v7.15.4).

---

## 🚢 Releasing (maintainers)

`.github/workflows/release.yml`: pushing a `v*` tag → full tests on Python 3.10–3.13 → build → checks (the tag must equal
`__version__`, `twine check --strict`, the Hermes entry works after a clean install, no tests or data files in the package)
→ publish to PyPI. It uses PyPI Trusted Publishing (OIDC); **no token is stored** in the repository. Running it by hand from
the Actions page only tests and builds.

One-time setup: PyPI project `nyx-memory` → Publishing → Add a new publisher → GitHub, with
Owner `sixgodgit` / Repository `nyx` / Workflow `release.yml` / Environment `pypi`.

To release: change `__version__` in `nexsandglass/core/sandglass_paths.py`, merge to main, then create a release on
[GitHub Releases](https://github.com/sixgodgit/nyx/releases/new) (tag `v<version>`, target `main`), or
`git tag v<version> && git push origin v<version>`. The pipeline fails if the tag and `__version__` differ.

---

## 🌱 Origins

Nyx started from NexLSL's open-source project **NexSandglass** (MIT, June 2026, v1.0–v2.9.9). The sandglass journal and
FTS5 full-text store, multi-route search (`search_router`), shadow sand, the WeaveThread knowledge graph, the L3 analyses
(persona / decision drift / scenes / emotion), the MCP server and the first Hermes plugin were designed and first written
there; the Python package name `nexsandglass` comes from it too.

Since v3.0 (July 2026) it has been maintained by sixgodgit: rebuilt as a modular package, then extended with runtime
orchestration and candidate promotion, the ID hub, the forgetting quarantine with verifiable deletion, bi-temporal facts,
source trust, utterance understanding, a Hermes provider following the official `MemoryProvider` interface, semantic search,
LongMemEval / LoCoMo evaluation, the secret filter and vault, and more (see [CHANGELOG.md](https://github.com/sixgodgit/nyx/blob/main/CHANGELOG.md)).
The full commit history is kept in the repository.

Thanks to NexLSL and the NexSandglass contributors.

---

## 📄 License

MIT; see [LICENSE](https://github.com/sixgodgit/nyx/blob/main/LICENSE). Copyright holders: NexSandglass (the original project) and sixgodgit (since v3.0).

**Made with 🧠 by Nyx**
