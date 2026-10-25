# Nyx — local long-term memory for Hermes

Nyx is a single-user, local memory provider. Writes involve no LLM: what you said is logged verbatim and indexed (FTS5, an inverted index, an optional embedding model). On top of that log, Nyx keeps:

- **Bi-temporal facts.** It can answer both "what is true now" and "what did I believe on day K".
- **Source trust.** Text from tools, delegation and bots never becomes a fact about you (a defence against memory poisoning).
- **Two-stage forgetting.** A forgotten memory sits in a quarantine you can restore from until it expires, then it is erased from the disk too.
- **Secrets filtering.** Tokens, API keys and passwords are replaced before anything is stored.
- **An optional encrypted vault.** It can hand a key you pasted back to you, with human confirmation and without the model seeing it.

Source, benchmarks and design notes: https://github.com/sixgodgit/nyx

## Install

```bash
hermes plugins install sixgodgit/nyx/hermes-plugin --enable
hermes config set memory.provider nyx
hermes gateway restart        # or restart Hermes
```

Hermes installs the declared dependency `nyx-memory` (PyPI) into its own environment. With the local embedding model, use `sixgodgit/nyx/hermes-plugin-vector` instead; its dependency is `nyx-memory[vector]` (sentence-transformers, about a 470 MB model download on first run).

Options: `hermes memory setup`. Terminal commands: `hermes nyx doctor`, `hermes nyx vault list`, `hermes nyx vault show v3`, `hermes nyx scrub`, `hermes nyx repair`, `hermes nyx status`.

## What it touches (disclosure)

- **Data.** Memory lives in `~/.neurobase` (`data_dir` in `$HERMES_HOME/nyx.json`; `NEXSANDBASE_HOME` overrides it). The optional vault's master key is `~/.config/nyx/vault.key` (0600, outside the data dir), or `NYX_VAULT_KEY`.
- **Network.** None by default. Each of these is opt-in:
  - `[vector]` downloads an embedding model from Hugging Face on first run. It sends no data.
  - `embedding_api_url` sends memory text and queries to the embedding API you configure (key: `EMBEDDING_API_KEY`).
  - `l3_llm: on` sends profile, scene and recent-decision snippets to DeepSeek or OpenRouter (key: `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`).
  - `NYX_UNDERSTAND_LLM` / `WTHREAD_LLM_EXTRACTION` with `LLM_EXTRACT_API_URL` send the sentence being extracted to the endpoint you configure.
- **Credentials.** Keys are read through `agent.secret_scope.get_secret` (profile-scoped). Nyx reads no `.env` or credential files. The optional `keyring` vault backend (`NYX_VAULT_BACKEND=keyring`) stores Nyx's own entries in the OS keychain under its own service name, and accepts only real system keychains (no plaintext backends).
- **Background work.** On start, a short warm-up thread loads indexes. With an embedding backend, a thread backfills vectors. Nothing outlives the process.
- **Approvals.** Revealing a vault entry asks for human confirmation through Hermes's `request_tool_approval`. When approvals are bypassed (`--yolo`, `approvals.mode: off`), Nyx asks the owner to reply with a one-time confirmation phrase instead; it never treats a bypass as consent. Under cron and subagents a reveal is refused.
- **Optional companion plugin `nyx-reveal`** (pip entry point, opt-in with `hermes plugins enable nyx-reveal`). It registers one hook, `transform_llm_output`, which swaps a reveal handle for the secret just before the reply is sent, so the model provider never sees the value.

---

## 中文

```bash
hermes plugins install sixgodgit/nyx/hermes-plugin --enable
hermes config set memory.provider nyx
```

装完重启 Hermes。选项在 `hermes memory setup` 里，终端命令是 `hermes nyx …`。完整说明见 [nexsandglass_hermes/README.md](https://github.com/sixgodgit/nyx/blob/main/nexsandglass_hermes/README.md)。
