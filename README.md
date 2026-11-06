# Nyx · nyx-memory

**English** | [简体中文](https://github.com/sixgodgit/nyx/blob/main/README.zh-CN.md)

> **Memory that stays with your agent for life** — single-user, local, no LLM on the write path.
> It remembers, finds, can be trusted, and can forget.

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python) ![License](https://img.shields.io/badge/License-MIT-green) ![Version](https://img.shields.io/badge/version-7.20.0-blue) ![Deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen)

Nyx is a long-term memory layer for agents. It plugs into [Hermes](https://github.com/NousResearch/hermes-agent)
(as a memory provider), into Claude Desktop / Claude Code or any other MCP client, or into your own Python program.
Every message is kept verbatim; on demand Nyx brings back what was said, with dates, and what is believed *now*.
All data is plain files on your machine.

## Why Nyx

| | How | Measured |
|---|---|---|
| **Finds things** | Full-text, inverted index, TF-IDF, entities and (optional) semantic search, fused; long memories are scored down by length so they don't bury the short message with the answer; questions like "how many times / how long between" get a few more results | LongMemEval-S (470 scored questions): the session holding the answer is in the top 5 **98.7%** of the time; LoCoMo evidence recall 0.573 (BM25 0.498); BEAM, 100K–1M-token conversations: evidence recall@10 **0.454** (BM25 0.333); at 10M tokens 0.326 (BM25 0.218). About 30–140 ms per recall |
| **Knows who you are** | What you told it about yourself — requests to the agent ("reply in Chinese, no emoji"), allergies and diet, family, home and work, likes — is in every session's system prompt in your own words; memories that match a question only through words like *what / 帮我* are not injected; a relative's facts stay theirs | Eight personal stories (v7.19.0): the facts the agent needed and saw went from 25–38% to **100%**, unrelated entries per question from 9 to 1–2, and unrelated questions get nothing injected 88% of the time (was 13–25%) |
| **Keeps time straight** | Bi-temporal facts: "when it was true" and "when it was learned" are stored separately | "I switched to a Geely back in February" is recorded as February, not as the day it was said; answers "what is believed now" and "what did I think in March" |
| **Can be trusted** | Source and trust are bound at write time: only what the user said themselves can become a rule; hearsay and web content come back labelled "unverified" | Poisoning drill: 0 of 16 attacks reach the agent's prompt unlabelled; 0 of the user's 8 normal messages are hurt |
| **You stay in charge** | In any chat, `/nyx pause` stops remembering until `/nyx resume` and `/nyx forget-last` takes back the last turn; in the terminal, `hermes nyx search / show / forget / restore / edit` find, read and correct memories without going through the model; `hermes nyx import` brings in what you told Hermes before Nyx was installed (v7.20.0) | Checked with Hermes's own plugin loader and command dispatch, as the terminal and the messaging gateway call it; an imported old "I live in Hangzhou" never overrides a newer "I moved to Shanghai" |
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

Two optional steps (v7.20.0):

```bash
hermes plugins enable nyx-commands    # /nyx status · pause · resume · forget-last in any chat
hermes nyx import                     # bring in your earlier Hermes conversations (lists them first, asks before importing)
```

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

From then on every turn is remembered and recalled automatically. The agent also gets tools such as `sandglass_search`
(with dates and a date range), `nyx_read` (one memory in full, with what was said around it), `nyx_belief` (what is
believed now / what was believed then), `nyx_forget` / `nyx_restore` (forget and restore) and `nyx_health`
(self-check). In the terminal: `hermes nyx search 花生 --since 2026-03`, `hermes nyx show <id>`,
`hermes nyx forget / restore / edit <id>`, `hermes nyx doctor` and more. Full guide:
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

27 tools; see [MCP tools](#mcp-tools).

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
4. Understanding: "switched to a Geely on Feb 25" → (user, car, Geely, from Feb 25, still true), stored as a bi-temporal fact; the old fact is not overwritten but gets an end date. Something said long ago but written now (an imported conversation, a correction) is filed as history and never overrides what was learned later (v7.20.0)
5. Full-text, inverted and (optional) semantic indexes are updated incrementally

**Every session** (the system prompt, v7.19.0)

What the user has said about themselves, in their own words and newest first: standing requests to the agent ("reply in
Chinese, no emoji"), health and diet (allergies, vegetarian), family, partner and pets, where they live and work, likes and
dislikes. Picked by rules from the owner's own messages only (no LLM); a forgotten memory leaves it at once. This is what
lets "what should I cook tonight?" see "I'm allergic to peanuts", which shares no word with it.

**Recall** (before every turn, entirely local)

1. Full-text (FTS5), inverted index, TF-IDF, entities and (optional) semantic vectors are searched
2. Fused ranking: complete literal matches first, semantics as support (half weight, only hits clearly above background count); memories longer than 300 characters are scored down by length, so a long reply cannot bury the short message that states the fact
3. Current beliefs are added ("user lives in Shanghai (since 2026-09-01)"); facts about family and friends are kept under their own subject ("user's mother lives in Qingdao")
4. Trust gate: memories from a tainted source stay out; unverified ones are labelled and cannot act as rules
5. Relevance gate (v7.19.0): an entry is injected only if it shares at least one content word with the question — matches through *what / how / 什么 / 帮我 / 几个* alone don't count — so nothing is injected when nothing is relevant
6. Injected into the agent with dates: up to 12 entries by default; up to 20 for "how many times / how long / which came first / before or after" questions

---

## 📋 Features

| Feature | Module | What it does |
|---|---|---|
| 🆔 **Journal + ID hub** | `core/memid.py`, `core/sandglass_log.py` | Every memory has a `mem_id` and a line span; tombstones keep deleted memories from coming back on rebuild |
| 🔍 **Multi-route search** | `core/search_router.py`, `core/semantic.py` | FTS5 / inverted index / TF-IDF / shadow sand + optional semantics (local multilingual model or an OpenAI-compatible API), RRF fusion |
| 🪪 **Always-on profile** | `core/profile.py` | The user's own statements about themselves (requests to the agent, health and diet, family, home and work, likes) in every session's system prompt; owner-only, trusted sources only, follows forgetting (v7.19.0) |
| 🚪 **Relevance gate** | `core/relevance.py` | Before injection: entries that match the question only through function words are dropped; nothing is injected when nothing is relevant (v7.19.0) |
| 🕰️ **Bi-temporal facts** | `engram/loops/temporal_fact.py`, `core/understand.py` | `as_of` asks about the world, `known_at` about what was believed then, `belief_timeline` about how a view changed; hypothetical times never pose as stated ones |
| 🛡️ **Source trust** | `core/provenance.py` | trusted / unverified / tainted; only what the user said can become a rule (against OWASP ASI06 memory poisoning) |
| 🕯️ **Forgetting quarantine** | `core/quarantine.py`, `core/erasure.py` | Two-stage forgetting: quarantine → restore, or erase when due; erasure goes through the journal, indexes, graph and vectors and can be verified independently |
| 🔑 **Secrets stay out** | `core/secret_filter.py`, `scrub.py` | Tokens / API keys / private keys / connection strings / passwords are hidden before writing; `scrub` hides already-stored ones in place and re-checks the raw bytes |
| 🔐 **Secret vault** (optional) | `core/secret_vault.py`, `vault.py` | The hidden values are stored encrypted and handed back to the user after a human confirmation; with direct reveal the model only gets a handle, never the value (tested on real Hermes) |
| 🩺 **Self-check** | `doctor.py` | Hub vs journal consistency, each index, forgetting integrity, quarantine due dates, rule poisoning — each with the reason |
| 🧯 **Check and repair** | `repair.py`, `core/forensics.py` | When the hub does not match the journal, checks record by record by content: adds what is missing, moves misplaced rows back, recognises overwritten memories, removes synthetic records and the "facts" derived from them; backs up first, never changes the journal |
| 🔌 **Hermes memory provider** | `nexsandglass_hermes/`, `core/memory_provider.py` | Implements Hermes's `MemoryProvider` interface; installed with `hermes plugins install sixgodgit/nyx/hermes-plugin --enable` |
| ⏸️ **`/nyx` in chat** | `nexsandglass_hermes_commands/`, `core/recording.py` | `status`, `pause` / `resume` (stop and restart remembering; recall keeps working), `forget-last [N]` (this chat's last turns to the quarantine, shown so you can check). Runs in Hermes, never through the model; in group chats only the owner can change anything (v7.20.0) |
| 🗂️ **Look up and correct** | `memory_cli.py`, `core/lookup.py` | `hermes nyx search` (date range with `--since` / `--until`), `show` (full text and the lines around it), `forget` (`--now` erases for good), `restore`, `edit` (the corrected words replace the old ones, at the original date); the agent's `sandglass_search` / `nyx_read` use the same code (v7.20.0) |
| 📥 **Import from Hermes** | `history_import.py` | Reads the profile's Hermes session database read-only and brings in your own conversations (local and private chats; not group chats, subagents or scheduled tasks) with their original dates; skips what Nyx already has; can resume (v7.20.0) |
| 🔌 **MCP server** | `interfaces/sandglass_mcp.py` | stdio, standard library only, 27 tools |
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
dv.sense("that ramen bar in Osaka we talked about")    # -> familiar=False, score 0.0 ("none of 3 tokens seen")
dv.sense("that Sichuan restaurant we talked about")    # -> familiar=True, score 0.5 (seen: sichuan)
dv.hunt("Sichuan")                     # -> [Phantom(token='sichuan', refs=['msg-1'], ...)]
```

Two layers: **Veil** (a 128 KiB Bloom filter) answers "has this word been seen", **Mist** (SQLite) records when,
where and how often it appeared. To be clear: it does not raise recall; it only splits misses into two kinds —
**good at classifying, limited at retrieving**. Common words are not tokens, so they never make a question familiar:
English *the, about, talked*… (v7.16.1), Chinese 我们, 聊过 and fragments such as 的那 (v7.16.2). With a long history,
ordinary content words have all been seen, so check *which* word was seen — a rare name is strong evidence, a generic
noun is weak. Measurements:
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
| `sandglass_search` / `sandglass_semantic` | Search with an id, date and speaker per result, `since` / `until` for a date range (v7.20.0) / synonym + SimHash + TF-IDF search |
| `nyx_read` | One memory in full, with when it was said, who said it and the lines around it (v7.20.0) |
| `sandglass_recent` / `sandglass_ping` | Last N entries by date, optionally within a date range / health check (`status=degraded` when the semantic index is broken) |
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

**Want to delete a memory?** In the terminal: `hermes nyx search …` to find it, then `hermes nyx forget <id>` (lists what
goes and asks; `--now` erases for good). In a chat: `/nyx forget-last` takes back the last turn (with `nyx-commands`
enabled); the agent's `nyx_forget` previews, then confirms. In Python `forget()`. Changed your mind: `hermes nyx restore`
lists the quarantine and `hermes nyx restore <id>` brings one back (or `nyx_restore` / `restore()`).

**Something it remembers is wrong?** `hermes nyx edit <id> "the right words"`: the corrected text is kept as yours, dated
when the original was said, and the old one goes to the quarantine (facts derived from it go with it).

**Want it to stop remembering for a while?** `/nyx pause` in the chat (helping someone else, a private matter);
`/nyx resume` turns it back on. While paused nothing is written (turns, the end-of-session catch-up, subagent results);
recall and what you explicitly ask for still work, and the agent is told it is paused. It stays paused across restarts.

**Used Hermes before installing Nyx?** `hermes nyx import` lists your earlier conversations and imports the ones you pick
(`--all` for everything) with their original dates. Only your own words and the agent's replies are imported — not group
chats, subagents, scheduled tasks, or the memory text Hermes injected; with several people in private chats, set
`owner_ids` first. Hermes's database is opened read-only, and running it again only adds what is new.
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
- **No end-to-end QA with a strong model yet**: the [Benchmark](#benchmark) numbers are retrieval metrics, what the agent sees, and a small local model (1.5B) choosing among four replies on PersonaMem; Mem0 / Zep and others report QA accuracy with large models (an LLM answers and an LLM judges)
- **Weak spots**: on LoCoMo multi-hop questions the agent sees only 41% of the evidence (48% with reranking); LongMemEval preference questions 0.71 (lower with reranking); BEAM summarization and event-ordering questions need 10+ messages spread over a long conversation and the agent sees 6–8% of them
- **Little English fact extraction**: rule-based extraction mostly covers Chinese; in English Nyx relies on retrieving the original text with its date. Without `[vector]`, English paraphrases that share no words with the memory are not found ("which editor do I use" does not find "I switched from VS Code to Zed"). Install `[vector]`, or enable `NYX_UNDERSTAND_LLM` for LLM extraction
- **Déjà Vu on a long history**: common words no longer count (English v7.16.1, Chinese v7.16.2), but ordinary content words accumulate. Of 10 unrelated test questions, after 2,000 English turns all 10 are still "familiar" through words like *teacher* or *workshop* (0 after 20 turns, 3 after 200; `benchmarks/dejavu_english_real.py`); after 2,000 Chinese chat messages 3 are (企鹅, 玻璃, 老师; `benchmarks/dejavu_chinese_real.py`)
- **The profile and the relevance gate are rules, not understanding** (v7.19.0): the profile picks up statements in common
  phrasings ("I'm allergic to…", "from now on…", "my daughter…", "我对…过敏", "以后…别…"); a preference only implied ("ugh,
  coriander again") is not in it. The gate is lexical: without `[vector]`, a relevant memory that shares no content word
  with the question is not injected (with `[vector]`, semantic hits pass)
- **Small default embedding model**: the local multilingual MiniLM is small and fast; with a stronger API model the fusion weights may need retuning (`NYX_EMBED_Z` / `NYX_EMBED_WEIGHT`)

---

<a id="benchmark"></a>

## 📊 Benchmark

Every evaluation feeds each turn through the Hermes provider's real write path. The first two (v7.19.0) score what a
personal agent sees about its user; the three public benchmarks after them measure **retrieval** only (no LLM,
reproducible). Semantic search uses the default local model `paraphrase-multilingual-MiniLM-L12-v2`
(`pip install "nyx-memory[vector]"`).

**Personal scenarios** (`benchmarks/personal_eval.py`, stories in `benchmarks/personal_cases.py`; results in
`benchmarks/results/personal_eval.json`). Eight stories, four Chinese and four English, of one person and their agent
over 40 days: 300 turns of work and small talk, and in between a diet, an allergy, standing requests to the agent
("reply in Chinese, no emoji"), family, home and a move. A new session then asks 62 questions: personal ones ("what
should I cook tonight?", "I have a fever, what medicine can I take?"), two work ones, and 16 unrelated ones ("how far is
the Moon?"). Scored on what the agent sees — the session's system-prompt block plus that turn's injected memories.
Four stories were used while developing; four were held out until the end. Lexical (the default install).

| | Dev, v7.18.1 | Dev, v7.19.0 | Held out, v7.18.1 | Held out, v7.19.0 |
|---|---|---|---|---|
| Facts the agent needed and saw | 38% | **100%** | 25% | **100%** |
| Personal questions where it saw everything it needed | 33% | **100%** | 18% | **100%** |
| Unrelated entries injected per question | 9.0 | **0.8** | 8.6 | **1.6** |
| Unrelated questions with nothing injected | 25% | **88%** | 13% | **88%** |
| Characters injected per question | 721 | **89** | 742 | **204** |
| Assistant lines in the system-prompt block | 17 | **0** | 17 | **0** |
| Hard-coded lines in the block (not from this user) | 4 | **0** | 5 | **0** |
| A relative's fact recorded as the user's | 1 | **0** | 3 | **0** |

- Most of the gain is the always-on profile: 79% of the facts seen are seen there, without sharing a word with the question
- The stories and the profile rules were written together, with all eight stories in view; the held-out half is held
  out for the relevance gate and the attribution fix, less so for the profile rules. PersonaMem (next) is the
  independent check
- What is left (held out): common English verbs still let unrelated entries through — "Where do I work now?" brings 11
  "why does X work in staging…" messages, "How do I look after succulents?" brings 20 "I looked at it…" replies. The two
  work questions still bring generic chat about production and migrations, which may or may not help
- v7.20.0: every number above is unchanged. The block is 6 characters longer on average (634 → 640) because a relative's
  health ("my mum has diabetes", "my wife is pregnant") is now listed under family rather than under the user's health

**PersonaMem** (`benchmarks/personamem_eval.py`; Jiang et al. 2025, "Know Me, Respond to Me", MIT; data not in the
repository: https://huggingface.co/datasets/bowen-upenn/PersonaMem-v1). 20 simulated users talk with a chatbot over many
sessions while their preferences change; 589 questions in the 32K-token split, each a user message mid-conversation and
four candidate replies, of which one fits this user best (recall what they shared, acknowledge the latest preference,
recommend in line with it, … 7 types). Nyx gets every turn up to the question through the real write path — not the
dataset's system messages, which describe the persona to the generator; a real agent never has them — and we take what
the agent would see. Results in `benchmarks/results/personamem.json`.

| | v7.18.1 | v7.19.0 |
|---|---|---|
| System-prompt block, median characters | 5,971 | **916** |
| Injected per turn, median characters | 3,737 | 3,685 |
| A local model picks the right reply, 200 questions (no memory: 0.370) | 0.425 | **0.445** |
| Zero-LLM proxy, all 589 questions (chance: 0.25) | 0.380 | **0.396** |

- The local model is Qwen2.5-1.5B-Instruct (llama.cpp, temperature 0). It reads only nyx's memory text, the user's
  message and the four replies; 200 questions spread evenly over the split. v7.19.0 against no memory: right on 21
  questions the model got wrong without memory, wrong on 6 it got right (sign test p = 0.006); v7.18.1 against no
  memory: 23 / 12 (p = 0.09). v7.19.0 against v7.18.1: 12 / 8 (p = 0.50) — no significant difference, with a block
  6.5× shorter
- By type the proxy rose on 4 of 7 (reasons behind a preference change 0.455 → 0.556, preference evolution
  0.182 → 0.236, facts the user shared 0.319 → 0.370, new scenarios 0.351 → 0.386) and fell on 3 (recommendations in line
  with preferences 0.509 → 0.382, suggesting new ideas 0.565 → 0.505, facts the user mentioned 0.706 → 0.471, 17
  questions). PersonaMem conversations open with the user introducing themselves, which the old block (the oldest ten
  messages) happened to hold, and the proxy counts shared words, so it favours the longer block. The model's answers show
  no such drop (recommendations 0.667 in both versions, new ideas 0.176 in both)
- v7.20.0: what the agent sees is identical to v7.19.0 on all 589 questions (proxy 0.396), apart from the block's entry
  count, which now counts memories instead of journal lines. One run on a heavily loaded machine differed on 19
  questions: a recall source that misses its time budget (1.5–3 s) is left out of that turn; a rerun matched exactly
- The proxy: for each reply, the words only it has; the share of them found in the memory text; the highest wins, ties
  count as a random pick
- Not comparable with PersonaMem scores reported elsewhere (TencentDB Agent Memory reports 48% → 76%): other models,
  other setups. Here a 1.5B model on a CPU, which also leans toward option (c); the point is the difference between
  versions


**BEAM** (`benchmarks/beam_eval.py`; Tavakoli et al., ICLR 2026, "Beyond a Million Tokens": 100 conversations of 100K
to 10M tokens, 10 question types, each question labelled with the messages that hold the answer; the 200 abstention
questions have no such messages and are not scored. Lexical only — the default install. The reference is plain BM25
over the same messages; results in `benchmarks/results/beam_retrieval.json`)

| Conversation length | Conversations | Messages each | nyx R@10 | BM25 R@10 | evidence the agent sees | recall p50 | write per message | disk |
|---|---|---|---|---|---|---|---|---|
| 100K tokens | 20 | 287 | **0.532** | 0.417 | 0.559 | 43 ms | 11 ms | 5 MB |
| 500K tokens | 35 | 1,087 | **0.494** | 0.368 | 0.515 | 54 ms | 10 ms | 23 MB |
| 1M tokens | 35 | 2,132 | **0.368** | 0.251 | 0.389 | 66 ms | 10 ms | 47 MB |
| 10M tokens | 10 | 20,870 | **0.326** | 0.218 | 0.350 | 149 ms | 12 ms | 500 MB |

| Question type (100K–1M) | Questions | nyx R@5 | BM25 R@5 | nyx R@10 | BM25 R@10 | evidence the agent sees |
|---|---|---|---|---|---|---|
| Contradiction resolution | 180 | **0.681** | 0.568 | **0.765** | 0.644 | 0.781 |
| Event ordering | 180 | **0.034** | 0.004 | **0.053** | 0.009 | 0.074 |
| Information extraction | 180 | **0.479** | 0.379 | **0.569** | 0.466 | 0.593 |
| Instruction following | 180 | **0.189** | 0.057 | **0.234** | 0.104 | 0.242 |
| Knowledge update | 179 | **0.678** | 0.445 | **0.770** | 0.588 | 0.791 |
| Multi-session reasoning | 180 | **0.384** | 0.212 | **0.483** | 0.305 | 0.529 |
| Preference following | 178 | **0.234** | 0.113 | **0.312** | 0.192 | 0.314 |
| Summarization | 172 | 0.034 | **0.044** | 0.064 | **0.078** | 0.079 |
| Temporal reasoning | 180 | **0.705** | 0.485 | **0.815** | 0.603 | 0.863 |
| **Overall** | 1609 | **0.382** | 0.257 | **0.454** | 0.333 | 0.476 |

- R@k = share of the labelled messages found in the top k; "evidence the agent sees" = among the entries injected into
  the agent (12, or 20 for "how many / how long / which first"); disk ≈ 10× the text written
- Writing one message takes about the same time however large the memory is: about 10 ms from 100K to 10M tokens
  (v7.18.1; in v7.18.0 it grew from 12 ms to 108 ms, so a 10M-token import took about 40 minutes, now about 4)
- Before v7.18.0 nyx recalled **less** than BM25 here (100K–1M R@10 0.211): BEAM messages average ~2,100 characters and
  long assistant replies outranked the short user message that states the fact. See the v7.18.0 changelog
- Summarization and event-ordering questions draw on 10+ messages spread over the whole conversation; no top-10 list
  covers that (BM25 neither). It is the weakest spot: the agent sees 6–8% of what those answers need
- This is retrieval; the BEAM paper reports LLM-judged answers, a different measure
- The relevance gate (v7.19.0) costs nothing here: evidence the agent sees is 0.476 before and after it (100K–1M).
  It does not help BEAM's 180 abstention questions either — none of them ends up with nothing injected: they are long
  English questions that share content words with the conversation, which a lexical gate cannot tell apart. Ranking is
  unchanged, so recall@k is identical to v7.18.1

**How the recalled memories are written into the prompt** (`benchmarks/beam_format_eval.py`, v7.18.0). Each injected
entry is a memory's first 300 characters. We tested the obvious alternative — for a long memory, the 300 characters that
cover the most words of the question — on the same recalled entries, 720 BEAM 100K–1M questions whose scoring points are
short facts. The scoring point appears within one injected entry:

| Question type | Questions | First 300 characters (kept) | Part about the question (tested) | Whole entries (reference) |
|---|---|---|---|---|
| Information extraction | 180 | 0.539 | 0.555 | 0.557 |
| Knowledge update | 180 | 0.781 | 0.781 | 0.797 |
| Multi-session reasoning | 180 | 0.310 | 0.314 | 0.349 |
| Temporal reasoning | 180 | 0.338 | 0.343 | 0.354 |
| **Overall** (characters injected) | 720 | 0.492 (4,358) | 0.498 (4,305) | 0.514 (21,165) |

Better on 18 questions, worse on 11 (sign test p = 0.26); at 10M tokens 0.498 vs 0.492. A local model
(Qwen2.5-1.5B-Instruct via llama.cpp, temperature 0) answering from the injected text alone, on 60 information-extraction
and 60 knowledge-update questions drawn at random, has the scoring point in its answer 0.336 of the time with the first
300 characters and 0.338 with the excerpt: 8 better, 8 worse. Even whole entries add only 2 points: on BEAM the fact is
usually in a short user message or early in a reply. So the format stays; recall ranking was the bottleneck.

**LongMemEval-S** (`benchmarks/longmemeval_eval.py`, 500 questions, ~50 sessions / 490 turns each; session-level recall as
officially defined, the 30 abstention questions not scored; results in `benchmarks/results/longmemeval_s_retrieval.json`)

| Type | Questions | any in top 5 | all in top 5 | all in top 10 | answer turns the agent sees | lexical only, all in top 5 (v7.18) |
|---|---|---|---|---|---|---|
| Knowledge update | 72 | 1.000 | 1.000 | 1.000 | 0.944 | 0.931 |
| Multi-session | 121 | 0.992 | 0.793 | 0.901 | 0.804 | 0.711 |
| Single-session (assistant) | 56 | 1.000 | 1.000 | 1.000 | 0.982 | 0.982 |
| Single-session (preference) | 30 | 0.900 | 0.900 | 0.900 | 0.706 | 0.600 |
| Single-session (user) | 64 | 1.000 | 1.000 | 1.000 | 0.961 | 1.000 |
| Temporal reasoning | 127 | 0.984 | 0.779 | 0.866 | 0.804 | 0.772 |
| **Overall** | 470 | **0.987** | **0.881** | **0.932** | **0.862** | 0.826 |

v7.19.0 (lexical): ranking unchanged; answer turns the agent sees 0.847 without the relevance gate and 0.849 with it;
of the 30 abstention questions, 1 ends up with nothing injected (the gate keeps a median of 18 of 22 recalled entries:
the questions are long and share content words with the sessions).

"any in top 5" = at least one answer session among the top 5 recalled; "all in top 5" = when the answer is spread over
several sessions, the top 5 found all of them; "answer turns the agent sees" = of the turns the dataset marks as holding
the answer, how many are among the entries injected into the agent (12 by default, 20 for "how many / how long / which
first" questions) — the closest to whether the agent can answer (v7.13.7).

The semantic columns are the full v7.17.0 run. v7.18.0 (long memories scored down by length) was re-run with semantic
search on every 4th question (116 scored), both versions on the same stores: any in top 5 0.983 → 0.991, all in top 5
0.888 → 0.922, all in top 10 0.931 → 0.966, answer turns the agent sees 0.863 → 0.930. Lexical only, full set:
all in top 5 0.781 → 0.826, answer turns the agent sees 0.738 → 0.847, any in top 5 0.955 → 0.945.

**LoCoMo** (`benchmarks/locomo_eval.py`, 10 long two-person conversations, 5,882 turns over months; 1,536 scored questions,
446 adversarial and 4 without evidence labels excluded as usual; the metric is how much of the labelled evidence is in the
top k; the reference is plain BM25 over the same sentences; results in `benchmarks/results/locomo_retrieval.json`)

| Category | Questions | nyx R@5 | lexical R@5 | BM25 R@5 | nyx R@10 | BM25 R@10 | evidence the agent sees |
|---|---|---|---|---|---|---|---|
| Multi-hop | 282 | **0.291** | 0.281 | 0.180 | **0.398** | 0.264 | 0.414 |
| Temporal | 321 | **0.680** | 0.660 | 0.611 | **0.741** | 0.678 | 0.756 |
| Open-domain | 92 | **0.273** | 0.271 | 0.243 | **0.352** | 0.272 | 0.358 |
| Single-hop | 841 | **0.660** | 0.615 | 0.590 | **0.720** | 0.655 | 0.727 |
| **Overall** | 1536 | **0.573** | 0.542 | 0.498 | **0.643** | 0.565 | 0.654 |

v7.19.0 (lexical): ranking unchanged; evidence the agent sees 0.626 with and without the relevance gate; the 446
adversarial questions still get entries injected (they ask about people and events that are in the conversation).

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
# BEAM (CC BY-SA 4.0, not in the repository): https://huggingface.co/datasets/Mohammadta/BEAM and Mohammadta/BEAM-10M
pip install pyarrow
python3 benchmarks/beam_eval.py 100K.parquet 500K.parquet 1M.parquet --workers 4 --no-semantic --json beam.json --views views.jsonl
python3 benchmarks/beam_eval.py 10M-00000-of-00002.parquet 10M-00001-of-00002.parquet --workers 4 --no-semantic --json beam_10m.json
python3 benchmarks/beam_format_eval.py views.jsonl            # add --llm with NYX_EVAL_API_URL / NYX_EVAL_MODEL for step 2
# Personal scenarios (stories in the repository) and PersonaMem (MIT, not in the repository)
python3 benchmarks/personal_eval.py --show
python3 benchmarks/personamem_eval.py questions_32k.csv shared_contexts_32k.jsonl --views views.jsonl
python3 benchmarks/personamem_eval.py questions_32k.csv --answer views.jsonl --llm --limit 200    # NYX_EVAL_API_URL / NYX_EVAL_MODEL
```

When the semantic backend is on but the index was not built, both harnesses fail loudly (v7.13.6) — lexical-only results are never recorded as "semantic on".

**Reproducible evaluations in the repository** (results in `benchmarks/results/`):

| Evaluation | What it measures | Version |
|---|---|---|
| `benchmarks/personal_eval.py` | Personal scenarios: in a new session, does the agent see the user's own statements it needs, how much unrelated text comes along, are family facts attributed to the right person (above) | v7.19.0, v7.20.0 |
| `benchmarks/personamem_eval.py` | PersonaMem: what the agent sees at each question, then a local model picks the best of four replies, with and without memory (above) | v7.19.0, v7.20.0 |
| `benchmarks/beam_eval.py` | BEAM retrieval evidence recall from 100K to 10M tokens (above), with a BM25 reference, write speed and disk | v7.18.0 |
| `benchmarks/beam_format_eval.py` | How recalled memories are written into the prompt: is the answer visible, does a model use it | v7.18.0 |
| `benchmarks/locomo_eval.py` | LoCoMo retrieval evidence recall (above), with a BM25 reference | v7.13.5 |
| `benchmarks/longitudinal_eval.py` | Half a year of one person's life: now / believed then / at some past moment, scored separately; includes held-out phrasings | v7.9–v7.11 |
| `benchmarks/poisoning_drill.py` | Memory poisoning: 0 of 16 attacks reach the prompt unlabelled, 0 of the user's 8 hurt | v7.8 |
| `benchmarks/forget_restore_drill.py` | 150 wrongly deleted → restored byte for byte; a purge is still irreversible | v7.6 |
| `tests/integration/hermes_real_check.py` | The full provider chain against real Hermes source (needs `HERMES_AGENT_SRC`) | v7.12 |
| `tests/integration/hermes_multiprofile_check.py` | One Hermes process, three profiles: settings stay per profile, a different `data_dir` is refused, `os.environ` / `sys.path` untouched | v7.17.0 |
| `benchmarks/dejavu_bench.py` | Déjà Vu familiar / unfamiliar classification (E2: English) | v7.5, v7.16.1 |
| `benchmarks/dejavu_english_real.py` | Déjà Vu false familiarity on real English conversation (LongMemEval-S) | v7.16.1 |
| `benchmarks/dejavu_chinese_real.py` | Déjà Vu false familiarity on real Chinese chit-chat (LCCC) | v7.16.2 |
| `benchmarks/recall_latency.py` | Hermes provider start-up, first turn and per-turn recall time with 30K / 150K memories; conversation mode follows a real rhythm (write one, query one) | v7.13.2–v7.13.3 |

> Older versions listed a "lexical recall 75%" from `tests/eval/run_eval.py` here. That script scored an in-memory list with
> its own simple tokenizer, **never going through nyx's retrieval**, so it said nothing about nyx's recall; it was removed.

---

## 📝 Recent updates

- **v7.20.0**: you stay in charge of what is remembered. `/nyx pause` / `resume` / `forget-last` / `status` in any chat
  (`hermes plugins enable nyx-commands`); `hermes nyx search / show / forget / restore / edit` in the terminal, never
  through the model; `hermes nyx import` brings in conversations from before Nyx with their original dates, and an old
  statement imported later never overrides a newer one. The agent's search results now carry dates and speakers, accept
  a date range, and `nyx_read` gives a memory in full with what was said around it (MCP too). Fixed: a memory restored
  from the quarantine could stay missing from the always-on profile. What the agent sees in the personal and PersonaMem
  evaluations is unchanged
- **v7.19.0**: built for a personal agent. Every session's system prompt now starts with what the user said about themselves
  (requests to the agent, health and diet, family, home and work, likes), in their own words; memories that match a question
  only through words like *what / 什么 / 帮我* are no longer injected, and nothing is injected when nothing is relevant; a
  relative's facts stay theirs ("my mum lives in Qingdao" is not the user's home). On eight personal stories the agent saw
  25–38% of the facts it needed and now sees 100%, with 1–2 unrelated entries per question instead of 9 (held-out half:
  1.6). Public benchmarks: retrieval unchanged, nothing lost to the gate
- **v7.18.1**: writing no longer slows down as the memory grows: about 10 ms per message from 100K to 10M tokens (before: 12 ms to 108 ms; BEAM). Recall unchanged
- **v7.18.0**: long memories no longer bury the short message that holds the answer (BEAM recall@10 0.211 → 0.454, now above BM25's 0.333; LongMemEval-S answer turns the agent sees 0.738 → 0.847, lexical); new BEAM benchmark up to 10M tokens; tested injecting the part of a long memory about the question instead of its first 300 characters — no measurable difference, so the format stays
- **v7.17.0**: per-profile settings in Hermes — one Hermes process serving several profiles no longer lets one profile's `nyx.json` (e.g. `l3_llm: on`) apply to the others; Nyx no longer writes to `os.environ` or `sys.path`; a profile with a different `data_dir` is refused instead of sharing; the provider is named `nyx` (from the Hermes plugin catalog review)
- **v7.16.2**: Déjà Vu no longer counts Chinese function words and cross-word fragments (我们, 聊过, 的那, 过的…): 20 remembered chat messages used to make 6 of 10 unrelated Chinese questions "familiar", now 0
- **v7.16.1**: Déjà Vu no longer counts common English words (*the, about, talked*…): one remembered English sentence used to make every unrelated English question "familiar"
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
