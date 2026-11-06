# Nyx — Hermes Agent memory provider

**English** | [简体中文](https://github.com/sixgodgit/nyx/blob/main/nexsandglass_hermes/README.zh-CN.md)

Implements Hermes's official `agent.memory_provider.MemoryProvider` contract, the same interface as Honcho / Mem0 / Hindsight.
Tested end to end against real Hermes source (`MemoryManager` + the plugin loader): `tests/integration/hermes_real_check.py`.

## Install

```bash
hermes plugins install sixgodgit/nyx/hermes-plugin --enable
hermes config set memory.provider nyx        # mainline Hermes already sets it on install; setting it again is harmless
```

Then restart Hermes (gateway: `hermes gateway restart`). Both commands only need `hermes`; nothing has to be installed in
the system Python first.

Hermes puts [`hermes-plugin/`](https://github.com/sixgodgit/nyx/tree/main/hermes-plugin) from this repository into
`$HERMES_HOME/plugins/nyx/` and installs the dependency it declares in `plugin.yaml`, `nyx-memory`, into **Hermes's own**
environment. After `hermes update` the dependency is installed again. This is what Hermes's documentation asks for — do
not inject a provider into its environment with pip: newer versions sync the environment from a lock file with their own
package manager, and whatever pip put there gets synced away.

For the local semantic-search model, install `sixgodgit/nyx/hermes-plugin-vector` instead: its dependency is
`nyx-memory[vector]`, and the first run downloads a ~470 MB model.

Verified on real Hermes: stable v2026.9.24 (a uv-built virtual environment without pip) and mainline (with the package
manager). On both, `hermes memory status` shows `Provider: nyx`, available, after installing.

### Older Hermes

"Older" means a version without `--no-deps` in `hermes plugins install --help`, such as hermes-agent 0.19.x from PyPI.
These do not install the dependencies a plugin declares and do not read pip entry points:

```bash
pip install nyx-memory                       # the same Python as Hermes
python3 -m nexsandglass_hermes install
```

`install` works on both:
- **Newer Hermes**: it simply runs the two `hermes` commands above for you. In a terminal you answer Hermes's dependency prompt yourself; outside a terminal it adds `--yes-deps` for you.
- **Older Hermes**: it finds the Python Hermes uses from the `hermes` command (including `#!/bin/sh` wrapper scripts), installs nyx-memory there if missing, writes the entry file in `plugins/nyx/`, sets `memory.provider: nyx`, and finally loads it once with Hermes's own loader to confirm it works.

| Option | Effect |
|---|---|
| `--vault` | Also turn on the secret vault: `hermes plugins enable nyx-reveal`, and `vault: on` in `nyx.json` |
| `--vector` | Also install semantic search (`hermes-plugin-vector` on newer Hermes, `[vector]` on older) |
| `--check` | Only check, change nothing |
| `--hermes-home DIR` | Hermes home to install into (with several profiles, install once per profile) |
| `--hermes-python PATH` | The Python Hermes uses (older Hermes, when it cannot be found) |
| `--source` / `--legacy` | Where newer Hermes installs the plugin from; force the older-Hermes way |
| `--no-pip` / `--no-config` | Do not install into Hermes's Python / do not change Hermes's config |

Installed before with the v7.15.0 install command, or copied the whole directory into `plugins/nyx/` following older
instructions? Run `install` once more and it switches to an installation Hermes manages itself.

## Configuration (`hermes memory setup` writes `$HERMES_HOME/nyx.json`)

| Key | Default | Meaning |
|---|---|---|
| `data_dir` | `~/.neurobase` | Memory directory. By default shared with nyx's MCP server / CLI (one memory per person). `NEXSANDBASE_HOME` wins. Restart after changing it |
| `prefetch_tokens` | `600` | Budget for each turn's automatic recall; 0 = no recall (only the drift / mood signal, when it is not steady) |
| `semantic` | `auto` | Semantic search: `auto` (an API if configured; the local model if `nyx-memory[vector]` is installed; otherwise off) / `local` / `api` / `off` |
| `embedding_model` | empty | Embedding model name; after a change, old vectors are dropped and rebuilt in the background |
| `embedding_api_url` / `embedding_api_key` | empty | OpenAI-compatible `/embeddings`; the key goes only to `.env` |
| `rerank` | `off` | `local` = rerank recall with a local multilingual cross-encoder (needs `[vector]`, ~470 MB download the first time). More accurate evidence recall (LoCoMo +4.7 points), but worse on preference questions and 0.3–1.5 s more per turn (CPU). See Benchmark in the main README |
| `vault` | `off` | Secret vault (v7.15.0): tokens / API keys / passwords pasted into chat are stored encrypted and handed back to the user after confirmation. Hermes already ships cryptography, nothing extra to install. See "Secret vault" below |
| `vault_reveal` | `direct` | `direct` = the value never passes through the model (needs the companion plugin `nyx-reveal`); `cli` = only in the terminal; `llm` = the value is given to the model to relay |
| `vault_kinds` | `all` | `tokens` = do not keep passwords |
| `lang` | `auto` | Language of terminal commands and `hermes memory setup`: `en` / `zh`; `auto` = Chinese when the system locale is Chinese (v7.16.0). Text the model reads is always English |
| `l3_llm` | `off` | `on` = the L3 analyses (persona / decision drift / phantom decisions) call DeepSeek or OpenRouter (`DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`, from the current profile) and send them relevant memory snippets, the persona and recent decisions. Before v7.15.2 this happened whenever a key was present |

## Terminal commands: `hermes nyx …` (v7.15.2)

Newer Hermes installs nyx-memory in its own isolated environment, so the system Python has no nyx and
`python3 -m nexsandglass.…` does not work. When nyx is the active memory provider, Hermes gains the `hermes nyx` commands
(data directory and vault switches follow the current profile's `nyx.json`):

| Command | Effect |
|---|---|
| `hermes nyx doctor` | Self-check: ✅ / ❌ per item, with the reason |
| `hermes nyx vault list` / `show v3 [--copy]` / `delete v3` / `gc` / `rekey` / `audit` / `status` | Secret vault |
| `hermes nyx scrub [--apply] [--to-vault]` | Secrets / passwords already stored: find them and hide them in place |
| `hermes nyx repair [--apply]` | When the hub does not match the journal: check and repair by content |
| `hermes nyx import [--list] [--all] [--session ID…] [--since DATE]` | Bring in earlier Hermes conversations (v7.20.0, below) |
| `hermes nyx search <words> [--since D] [--until D] [-n N]` | Find memories, most relevant first, with date and speaker; dates are YYYY-MM-DD, YYYY-MM or YYYY, inclusive. No words + a date range = what was said then (v7.20.0) |
| `hermes nyx show <id> [--around N]` | One memory in full: date, speaker, trust, live / in the quarantine / erased, and the lines around it (v7.20.0) |
| `hermes nyx forget <id…> \| --contains TEXT [--now] [--yes]` | To the quarantine after listing what goes and asking; `--now` erases for good (type `erase` to confirm) (v7.20.0) |
| `hermes nyx restore [<id…>]` | No id: what is in the quarantine and until when; with ids: bring them back (v7.20.0) |
| `hermes nyx edit <id> [new text]` | Correct a memory: the new text is kept as your words at the original date, the old one goes to the quarantine (v7.20.0) |
| `hermes nyx status` | Version, Python, data directory, vault |

Commands the agent passes on to you (health warnings, "get it in the terminal") are written as `hermes nyx …` too.
Every command takes `--json`. None of them goes through the model.

## `/nyx` in any chat (v7.20.0)

Slash commands need a regular Hermes plugin (a memory provider cannot register them), so they ship as a companion plugin in
nyx-memory. Enable it once:

```bash
hermes plugins enable nyx-commands
```

| Command | Effect |
|---|---|
| `/nyx status` | Version, number of memories, recording on or paused, what the profile holds |
| `/nyx pause` | Stop remembering: turns, the end-of-session catch-up, subagent results and the built-in memory mirror are not written. Recall and explicit requests ("forget that") keep working, and the agent is told recording is paused. Stays paused across restarts and sessions |
| `/nyx resume` | Start remembering again |
| `/nyx forget-last [N]` | This chat's last N turns (default 1, at most 20) to the quarantine; the forgotten lines are shown so you can check, with the command that brings them back |

They run in Hermes and the reply stays in the chat; the model never sees them. Who may use them: anyone on the local
machine (CLI, TUI, desktop) and in a private chat; in group chats and channels only `status`, because anyone there can
type a command. With `owner_ids` set, only the owner can pause, resume and forget, also in groups. Bots never can.

## Importing earlier conversations: `hermes nyx import` (v7.20.0)

If you used Hermes before installing Nyx, what you told it is in Hermes's session database (`$HERMES_HOME/state.db`).
`hermes nyx import` opens it **read-only**, lists the conversations it can import and asks which (`--all` for every
one, `--session` for some, `--list` to only look):

- **Imported**: your local sessions (CLI, TUI, desktop) and private chats — your words and the agent's replies, with their
  original dates, through the normal write path (secrets hidden, facts extracted, the profile updated)
- **Not imported**: group chats and channels (other people), subagents and scheduled tasks (their "user" is the agent),
  API, batch and test sessions; tool calls; messages that were rewound, compression summaries and system-inserted
  continuations; and the memory text Hermes injected into what the model saw — only what you typed
- With `owner_ids` set, only the owner's private chats. Without it, if the private chats come from several people,
  `--all` is refused: Nyx cannot tell which of them is you
- What Nyx already has (the same sentence within a day) is skipped, and each session's progress is kept, so running it
  again only adds what is new
- Older statements never override newer ones: "I live in Hangzhou" from January, imported after you said "I moved to
  Shanghai" in October, is filed as where you lived before

## Secret vault (v7.15.0, off by default)

By default, a secret pasted into chat is replaced with a placeholder before it is written to memory, and the value is
dropped. With `vault` on, the value is not dropped but stored encrypted in the vault (the master key is in
`~/.config/nyx/vault.key`, outside the data directory), and the placeholder gets an id, e.g. `[hidden: HF token …ab12 #v3]`
(before v7.16.0: `[已隐去: …]`, still recognised).

When the user says "send me the HF token I gave you", the agent calls `nyx_secret_reveal(v3)`, and then:

1. Hermes asks for human approval. If approvals are off (`/yolo` or `approvals.mode: off`), the user replies with a confirmation phrase instead (e.g. `confirm v3 123456`; the Chinese form `确认 v3 123456` works too).
2. The model only gets a handle. Just before the reply goes to the user, the companion plugin swaps the handle for the value, so the model provider never sees it and Hermes's session log stores the handle.

To enable it (Hermes already ships cryptography, nothing extra to install):

```bash
hermes plugins enable nyx-reveal      # then set vault to on in hermes memory setup, and restart Hermes
```

The safest way is always the terminal: `hermes nyx vault show v3` (outside Hermes: `python3 -m nexsandglass.vault show v3`).
The value passes through no model and no chat platform.

Two things the vault cannot protect against:
- The moment a secret is first pasted into chat, the value has already been sent to the model provider and stays on the chat platform, so any pasted secret should be rotated.
- When the bot is in a group chat, anyone in the group counts as the user.

Threat model and tests: [docs/secret-vault.md](https://github.com/sixgodgit/nyx/blob/main/docs/secret-vault.md).

## What happens each session and each turn

- **system prompt** (once per session): `[About the user]` — what the user has said about themselves, in their own words
  and newest first: standing requests to the agent, health and diet, family / partner / pets, home and work, likes and
  dislikes (v7.19.0; `core/profile.py`). Below it, when there is something to say: the persona the user built, decision
  drift, open to-dos and rules
- **prefetch**: a fresh local recall for this turn's question (about 30–140 ms per turn), each entry with the date it was said;
  for questions about where the user lives / works / their job, the current bi-temporal belief is added
  ("user lives in Shanghai (since 2026-09-01)"). Only entries sharing at least one content word with the question are
  injected, so an unrelated question gets nothing (v7.19.0). Up to 12 entries by default; up to 20 for "how many times /
  how long / which came first / before or after" questions (`NYX_BREADTH_LINES`, 0 = off). Hermes shows `🧠 Nyx` with the
  number of memories recalled
- **sync_turn**: the message goes to the journal (audit log) + promotion gate + fact extraction; the source is bound from `turn_author`
- **subagent / cron / flush contexts are read-only**: nothing is written, and write tools refuse

## Tools

| Tool | Effect |
|---|---|
| `sandglass_search` / `sandglass_recent` | Search / the most recent memories. Each result has an id, the date it was said and who said it; `since` / `until` (YYYY-MM-DD, YYYY-MM or YYYY, inclusive) limit it to a period; texts over 300 characters are cut (`more: true`). Sources other than the user and the agent are labelled `(unverified · source: …)` (dates and range v7.20.0) |
| `nyx_read` | One memory in full, with the lines said around it (`around`, default 3). Forgotten memories and ones flagged as injection are not shown (v7.20.0) |
| `nyx_belief` | Bi-temporal facts: what is believed now; what was true on `as_of`; what was believed at `known_at`; `history` = how it changed |
| `nyx_forget` | Forget → **quarantine** (preview first, then `confirm=true`; at most 20 at a time). The agent cannot erase permanently |
| `nyx_restore` / `nyx_quarantine` | Restore from the quarantine as-is / list the quarantine |
| `fact_store` / `fact_feedback` | Shadow-sand fact store and trust feedback |
| `sandglass_offset` / `sandglass_echo` | The user's decision drift / sentiment |
| `nyx_health` | Self-check (`quick=true` checks only the semantic index). Called when the system prompt shows "⚠️ Nyx health" |

## Self-check

A failing subsystem such as the semantic index does not take the conversation down (lexical recall keeps working), but it
is **never silent**: the system prompt gets a line "⚠️ Nyx health: …" and the agent tells the user. In the terminal,
`hermes nyx doctor` (or `python3 -m nexsandglass.doctor`) shows every item.

## Known limitations

- Several profiles in one Hermes process (gateway, desktop): each profile's `nyx.json` settings (`l3_llm`, `vault`, `semantic`, `lang`…) apply only to that profile's calls (v7.17.0; before, the first profile's settings were copied into the process environment and applied to all). The data directory is one per process: profiles with the same `data_dir` share memories; a profile configured with a different `data_dir` is reported unavailable in that process — give it the same `data_dir`, or run it in its own process. Sharing a `data_dir` means sharing one memory: a profile that turns on `l3_llm` or an embedding API sends from that shared memory during its own calls, which can include what was said in the other profile. Profiles that must stay apart need their own `data_dir` (and so their own process)
- Semantic search needs an embedding backend (local model or API); without one, paraphrases are not recalled (asking about "company" when the message said "joined Contoso")
- After upgrading from v7.13.2 or earlier, the first start fills vectors for all past memories in the background (a few thousand take tens of minutes on a local CPU model); semantic recall becomes complete gradually
- Quarantine purges are not automatic: run `python3 -c "from nexsandglass.core import erasure; print(erasure.purge(apply=True))"` regularly; the self-check reminds you when purges are overdue
- Machines set up with the v3.x `install.sh`: delete `~/.hermes/plugins/memory/nexsandglass` and `~/.hermes/plugins/sandglass` (old copied plugins that record every message twice)
