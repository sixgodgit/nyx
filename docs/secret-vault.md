# Secret vault (option 3)

**English** | [简体中文](secret-vault.zh-CN.md)

> Status: **implemented, v7.15.0**. Based on the nyx source and the hermes-agent 0.19.0 source; Hermes line numbers below refer to 0.19.0.
> Off by default. When it is not turned on, behaviour is byte-for-byte the same as v7.14.0.
> Designed for personal use: one user (the owner), no distinction between speakers (see §2).

## 0. In one sentence

Since v7.14.0, tokens, API keys and passwords pasted into chat are replaced with a placeholder before they are written to memory, and the value is dropped.

The vault is an option the owner turns on: the value is not dropped but stored encrypted. When the owner explicitly asks
for it, it is handed back after a human confirmation, **while keeping the value away from the model provider as far as possible**.

## 1. What it does and does not do

**Does**
- Stores the secrets the owner pastes, encrypted. Only user-side messages are collected; nothing from assistant replies or tool output. The placeholder gets an id, e.g. `[hidden: HF token …ab12 #v3]` (stored before v7.16.0: `[已隐去: …]`, still recognised).
- When the owner asks "what was that HF token from last time", the agent can find the right entry without the value: id, kind, last 4 characters, when it was saved, the surrounding context.
- When the owner wants the value, there are three ways to get it back (§6). Each needs a human confirmation, is rate-limited, and is audited.
- Follows forgetting: when the source memory goes to the quarantine the entry cannot be revealed; when the source memory is purged, the entry is purged too (§8).

**Does not**
- It is not a password manager. Keep important credentials in 1Password, Bitwarden or the system keychain. The vault is a safety net for "I pasted it in chat once and want it back".
- It does not let the agent use secrets to call other services. Keys the agent itself needs belong in Hermes's environment variables or secret configuration.
- It cannot do anything about what has already leaked. The moment a secret is pasted into chat, the value has been sent to the model provider and stays in Hermes's own session log and on the chat platform (§9).
- It cannot recover values already erased by v7.14.0 `scrub --apply`. Erased is gone.

## 2. Premise: personal use, speakers are not verified

nyx is personal memory: every user-side message counts as the owner's, and the vault does not decide "who said this".
So the gates in §7 check "**is the owner asking for it themselves**" (an explicit request plus a human confirmation), not "is this the owner".

That gives one usage rule, stated in the docs: with the vault on, do not put the bot in a group chat, or anyone in the group counts as the owner (§9).

A side note, unrelated to the vault and handled separately: the README said `owner_ids` can tell the owner from others in
group chats, but Hermes 0.19.0 never passes speaker information to the memory provider.
- `on_turn_start` only passes the turn and the message (`agent/turn_context.py:791`).
- `sync_all` only passes `session_id` and `messages` (`agent/memory_manager.py:666`).
- So the `author_id` / `turn_author` nyx reads is always empty, and the option has no effect under Hermes.

## 3. Overall structure

```
Write (sync_turn, Hermes's serial background thread)
  user-side message ──► secret_filter.scrub(vault=Collector())
                      │  each match: value ──► secret_vault.put() ──► returns 3
                      ▼
                    "…the key is [hidden: HF token …ab12 #v3]" ──► journal / hub / indexes / vectors (as in v7.14.0)
                      the value only exists as ciphertext in vault.db (or the system keychain)

Reveal
  ① CLI       python3 -m nexsandglass.vault show v3          the value only appears in the owner's terminal
  ② direct    nyx_secret_reveal(v3) ─► the model only gets a handle ⟦nyx:v3⟧
              the nyx-reveal plugin's transform_llm_output hook ─► swaps the handle for the value before the reply reaches the owner
  ③ via model nyx_secret_reveal(v3) ─► the tool result contains the value (off by default)
```

Where the code is:
- `core/secret_vault.py`: storage, encryption, reference state, audit, rate limits, direct handles, confirmation codes.
- `scrub(vault=...)` in `core/secret_filter.py`: adds the id to the placeholder.
- `core/memory_provider.py`: the two tools and the gates.
- `nexsandglass_hermes_reveal/`: the Hermes companion plugin.
- `nexsandglass/vault.py`: the command line.

The module is not called `vault` because `features/sandglass_vault.py` is the inverted index; this avoids a name clash.

## 4. What goes into the vault

All of these must hold:

1. `NYX_VAULT=on`, and a backend is available (§5). Otherwise, as in v7.14.0, values are only hidden, not kept.
2. The primary agent is writing (`agent_context == primary`), not a subagent. Subagents do not write memory anyway.
3. It is a user-side message (`user_content` in `sync_turn`).
4. It is a complete match. Truncated tokens (`[hidden: secret (truncated)]`) are not kept, and neither is `<private>…</private>` (which means "do not remember").
5. The level allows it: with `NYX_SECRET_FILTER=tokens` passwords are not hidden in the first place, so they are not kept; with `NYX_VAULT_KINDS=tokens` only tokens, API keys and private keys are kept, not passwords.

Secrets in assistant replies, tool output, delegation results and Hermes's built-in memory writes (`on_memory_write`) are still only hidden, not kept.

**Deduplication**: pasting the same value again gives the same id (looked up by HMAC, see §5). If an assistant reply contains
a value already in the vault, its placeholder gets that id too, but no new entry is added.

**Revealed values are hidden literally once more.** With direct reveal, the hook appends the value to the reply, and Hermes
then hands the rewritten reply to `sync_turn` (`turn_finalizer.py:584`, which runs after the hook). A form like
"(password): `hunter2`" is not necessarily recognised by the filter rules. So revealed values are remembered in memory for
an hour: when they appear again in text about to be written (the model repeating it, the owner pasting it back), they are
replaced with the numbered placeholder (`secret_vault.redact_revealed`).

**What each entry stores** (table `secrets` in `vault.db`)

| Column | Meaning |
|---|---|
| `id` | Auto-increment id, shown as `v3`. **Deleted ids are never reused**, so `#v3` in an old memory can never point at a different secret |
| `kind` / `label` / `last4` | The same as the placeholder, e.g. `huggingface` / `HF token` / `ab12`. Passwords keep no last 4 |
| `context` | The text around the placeholder after the message was hidden, e.g. "set up Hugging Face for me, the key is [hidden: …]". The journal already has it, so it is no more sensitive than the journal |
| `created_at` | When it was saved |
| `mac` | HMAC-SHA256(dedup subkey, value), with a unique index. A keyed HMAC rather than a plain hash, so that someone holding vault.db cannot guess short passwords offline |
| `backend`, `nonce`, `ct` | Which backend holds it. With the encrypted file this is AES-256-GCM ciphertext; with the keychain `ct` is empty |
| `revealed`, `last_revealed_at` | Number of reveals, time of the last reveal |

**State is not stored in the table; it is computed from references:**
- A memory that has not been forgotten mentions `#v3` → revealable.
- Only memories in the quarantine mention it → not revealable.
- Nothing mentions it → removed by `gc()` (§8).

That way, when the same value appears in two memories, forgetting one does not affect the other.

## 5. Storage

**Backends** (`NYX_VAULT_BACKEND`, default `auto`)

| Backend | Where the value is | Dependency | When auto picks it |
|---|---|---|---|
| `file` | Ciphertext in `vault.db` | `pip install 'nyx-memory[vault]'` (cryptography) | When a working cryptography is installed |
| `keyring` | System keychain: macOS Keychain, Windows Credential Manager, Linux Secret Service / KWallet. Service name `nyx-vault:<hash of the db path>`, account `v3` | `pip install 'nyx-memory[vault-keyring]'` | When there is no cryptography but a trusted keychain exists |

auto prefers the encrypted file over the keychain for two reasons:
- The gateway usually runs on a machine without a desktop, which usually has no keychain.
- On a desktop, a background thread touching the keychain may pop up an authorisation dialog and block writes.

Only real system keychains are accepted. Things like `keyrings.alt`'s PlaintextKeyring write the "secret" in plain text into the home directory; those are rejected.

**No plaintext fallback**: when neither backend is available, it behaves as `off` (hide only) and `doctor` says why. A
broken cryptography counts as not installed. Observed: when the system package's cryptography lacks `_cffi_backend`, the
import raises pyo3's `PanicException`, which derives from `BaseException`, so `except Exception` does not catch it.

**Encrypted-file details**
- The master key is 32 random bytes in `~/.config/nyx/vault.key` (respects `XDG_CONFIG_HOME`), file mode 0600, directory 0700. It is **not in the data directory**, so backing up, syncing or copying the data directory does not take the key along. It can also come from the environment variable `NYX_VAULT_KEY` (base64).
- A key file with permissions wider than 0600 (readable by group or others) is refused, the way ssh treats private keys.
- HKDF derives two subkeys from the master key, one for encryption and one for the dedup HMAC. The `meta` table stores a key check value, so a wrong key reports "wrong key" instead of decrypting to garbage.
- Encryption is AES-256-GCM with a random 96-bit nonce. The associated data is `nyx-vault-v1|<id>|<kind>`, binding the ciphertext to its row: ciphertext moved from another row does not decrypt.
- `vault.db` is SQLite with file mode 0600. After an entry is deleted it is VACUUMed, and `erasure.wipe_free_space()` covers it too.
- The v7.14.0 raw-byte re-check (`secret_scrub._bytes_scan`) scans `vault.db` as usual. It only holds ciphertext, so it finds 0 — that is the acceptance check for "the value never lands on disk".

**Backups**: `backup_paths()` includes `vault.db` (ciphertext only) but not the key. When restoring on a new machine without
the key, listing works and revealing says "no vault key on this machine". `vault status` reminds the owner to keep a copy of
the key, for example in a password manager.

## 6. Three ways to reveal

### 6.1 Command line (safest, always available)

```
python3 -m nexsandglass.vault status            # on or off, backend, key file (with permissions), entry count, companion plugin
python3 -m nexsandglass.vault list [keyword]    # id, kind, last 4, time, context, state; never the value
python3 -m nexsandglass.vault show v3           # print the value to the terminal
python3 -m nexsandglass.vault show v3 --copy    # to the clipboard (pbcopy / wl-copy / xclip / xsel / clip.exe, whichever exists), cleared after 30 s
python3 -m nexsandglass.vault delete v3         # purge
python3 -m nexsandglass.vault gc                # remove entries whose source memories no longer exist
python3 -m nexsandglass.vault rekey             # new master key, re-encrypt everything
python3 -m nexsandglass.vault audit             # recent reveals / deletions (without values)
```

The value passes through no model, no chat platform and no Hermes session log. The precondition is that the owner can log in to this machine.

On newer Hermes, nyx-memory lives in Hermes's own isolated environment and the system Python has no nyx, so use
`hermes nyx vault …` (since v7.15.2). The subcommands are the same, e.g. `hermes nyx vault show v3`. Commands the agent
passes on to you are written this way too (`core/hints.py`).

### 6.2 Direct (the default under Hermes)

Idea: the model only gets a handle; swapping it for the value happens before Hermes sends the reply to the owner.

1. The model calls `nyx_secret_reveal(id="v3")`, which first goes through the gates in §7.
2. The tool result **has no value**, only: `{"status": "queued", "handle": "⟦nyx:v3⟧", "note": "The secret does not pass through you…"}`.
3. nyx keeps a pending entry in process memory: session id, entry id, value. Valid for 10 minutes, usable once.
4. At the end of the turn Hermes calls the plugin hook `transform_llm_output(response_text, session_id, …)` (`agent/turn_finalizer.py:455`).
   - nyx's hook replaces the handle in the reply with the value, in a code block for easy copying.
   - If the model did not write the handle, it appends a line "🔑 v3 (HF token, saved 2026-10-20): …".
   - Handles from other sessions are not replaced.
5. When the reply was rewritten, the gateway re-sends the final version (comment at `gateway/run.py:22061`), so on streaming platforms the owner still gets the replaced message.

**Companion plugin `nyx-reveal`**: Hermes loads memory providers separately as "exclusive plugins", and the `register_hook`
it gives them is a no-op (`plugins/memory/__init__.py:343`), so the hook cannot be registered from the memory provider and
needs a separate regular plugin.
- It is installed with nyx-memory through the pip entry point `hermes_agent.plugins → nyx-reveal` (`hermes_cli/plugins.py:1658` scans that entry-point group).
- Hermes user plugins are disabled by default. `python3 -m nexsandglass_hermes install --vault` also runs `hermes plugins enable nyx-reveal`; or run it once yourself and restart Hermes.
- The plugin source must not contain `register_memory_provider` or `MemoryProvider`, or Hermes takes it for a memory provider too and does not load its hooks (`hermes_cli/plugins.py:1594`). A test checks this.

Without the companion plugin installed or enabled, `nyx_secret_reveal` does not fall back to 6.3; it returns
`{"status": "use_cli", "command": "python3 -m nexsandglass.vault show v3", ...}` so the model tells the owner the command.

**Verified in real Hermes** (`benchmarks/vault_hermes_e2e.py`, hermes-agent 0.19.0). With a real AIAgent, real plugin loading,
real terminal approval and a real session store, only the model replaced by a local fake server that records every request body:
- 7 requests sent to the model, 0 containing the value.
- The confirmation code was never sent to the model before the owner typed it.
- Hermes session store (state.db): 9 rows, 4 with the handle (positive control: it was stored), 0 with the value.
- A raw-byte scan of the Hermes directory and the nyx data directory finds the value nowhere.

### 6.3 Via the model (off by default; only with `NYX_VAULT_REVEAL=llm`)

The tool result contains the value and the model relays it. The cost is that the value:
- is sent to the model provider;
- enters Hermes's session log (under `~/.hermes`);
- stays in the context of the following turns until compaction (Hermes masks it when compacting).

Only for people who explicitly accept that and do not want the companion plugin. In nyx's own memory the value is still hidden literally (§4).

## 7. Tools and gates

The two tools only appear in the tool list when `NYX_VAULT=on`, so not everyone gets extra tools.

| Tool | Returns | Notes |
|---|---|---|
| `nyx_secret_list(query?)` | id, kind, last 4, date saved, context, state (revealable / source in quarantine / source gone), reveal count | Never the value |
| `nyx_secret_reveal(id)` | `queued` / `value` (6.3 only) / `need_confirm` / `use_cli` / `denied` (with the reason) / `unavailable` | The value only leaves as described in §6 |

When on, the system prompt gets one line: "`#vN` in a memory is a secret-vault entry id. Call nyx_secret_reveal only when
the user explicitly asks to see the secret; never guess, reconstruct or repeat it."

**Gates of `nyx_secret_reveal`**: checked in order; failing any returns `denied`.

| # | Gate | Against |
|---|---|---|
| 1 | Must be the primary agent, not a subagent or a scheduled job. Checked via `agent_context`; scheduled jobs via `HERMES_CRON_SESSION`, the same way Hermes's approvals decide (`tools/approval.py:241`) | Unattended flows revealing on the owner's behalf |
| 2 | The owner's **own words this turn** contain an explicit request: both a verb such as "give me / tell me / send me / show / what is…" and a noun such as "token / key / secret / password / v3…". The owner having just replied with the confirmation phrase also counts | Prompt injection hidden in recalled memories, web pages or files making the agent reveal on its own |
| — | The entry exists and its source memory has not been forgotten | |
| 3 | Human confirmation (two forms, below) | The last line when everything above was bypassed; the model cannot forge it |
| 4 | Rate: at most 3 reveals per hour and 10 per day through the tool (the CLI does not count); 3 wrong confirmation phrases in a row lock for 15 minutes | Repeated probing |
| 5 | Audit: recorded in the `audit` table of `vault.db` (see `vault audit`) — time, id, action, reveal path, platform, result, reason; **never the value** | Being able to check afterwards |

**Gate 3: human confirmation**
- **Preferred: Hermes's own approval**: call `tools.approval.request_tool_approval("nyx_secret_reveal", reason="Show vault entry v3 (HF token …ab12, saved 2026-10-20)", rule_key="nyx_vault:<one-time random>")` (`tools/approval.py:2940`).
  - In the CLI it is a confirmation prompt in the terminal; in the gateway an approval message on the chat platform. 60-second default timeout; timeout, denial and errors all count as denial.
  - This is the entry point Hermes provides for plugins to escalate a tool call to human approval, and the model cannot skip it. The approval prompt only shows id, kind, last 4 and date; tested not to contain the value.
  - `rule_key` is a fresh random value every time. The approval UI offers "allow for this session" and "always allow"; with a fixed key, one click on "always" would mean no more questions for any future reveal. The cost is that clicking "always" leaves an unused entry in Hermes's allow list.
  - **Not used when approvals are off altogether.** `--yolo`, `/yolo` in a session and `approvals.mode: off` all make this approval pass immediately (`is_approval_bypass_active()` at `tools/approval.py:2472`). The owner turning off command approvals is not consent to the agent handing out secrets, so the confirmation code is used instead.
- **Confirmation code**: used when Hermes's approval is unavailable or turned off.
  - nyx generates 6 digits and asks the owner to reply with them verbatim in the next message, e.g. "confirm v3 482913" (the Chinese form "确认 v3 482913" is accepted too).
  - With 6.2 the hook appends this prompt to the end of the reply directly, so the model never sees the code; with 6.3 the model relays it.
  - The next turn's `on_turn_start` (agent thread) checks it: same session, within 5 minutes, exact match → `nyx_secret_reveal(v3)` is allowed once in that turn.
  - The model claiming "the owner already confirmed" in the tool arguments does nothing; only the owner's own words this turn count (covered by tests).

## 8. Lifecycle

| Event | Vault |
|---|---|
| `nyx_forget` / `forget` puts the source memory in the quarantine | Only quarantined memories mention it; state becomes "source memory in quarantine", not revealable |
| `nyx_restore` | A live memory mentions it again; revealable |
| Quarantine `purge` (due, or explicit) | Of the ids mentioned by the purged memories, those no longer mentioned anywhere are purged too, without waiting (`drop_unreferenced`) |
| `forget(mode="purge")`, immediate purge | Same as above |
| `wipe_free_space()` (after every purge) | `gc()` removes entries whose source no longer exists and which were saved more than 2 minutes ago (the few milliseconds before the journal write do not count as orphaned), then VACUUMs `vault.db` |
| `vault delete v3` | Purged directly. Placeholders in the source memories stay; the id shows as "deleted" |
| `scrub --apply --to-vault` | Handles plaintext stored before v7.14.0 that was never cleaned: journal entries with `sender == user` go into the vault first and their placeholders get ids; everything else is only hidden as before. Collection happens in one separate pass, and every later step only looks up, so the journal and the hub get the same id (otherwise their texts would not match). Without `--to-vault` nothing changes |
| `doctor` / `nyx_health` | New `vault` item. Off is fine; on but unusable is a failure; entries whose source was purged but which are still in the vault are a failure too, with a hint to run `vault gc`. With direct reveal it also reports whether the companion plugin is enabled (reads `$HERMES_HOME/config.yaml`) |

## 9. Threat model

**Protects against**
- The data directory being copied, synced or backed up: it only holds ciphertext, and the key is not in the data directory.
- Memories being recalled, put into prompts or sent to an embedding service: they only hold the placeholder (as in v7.14.0).
- Prompt injection in recalled content, subagents or scheduled jobs trying to make the agent hand out a secret: gates 1–3 in §7.
- With 6.2, the model provider seeing the value in the reveal turn: the tool result only has a handle. Verified in real Hermes (§6.2).
- The owner clicking "always allow" once and never being asked again: a fresh `rule_key` every time.
- The owner having turned off command approvals: nyx detects it and uses the confirmation code.

**Does not protect against**
- **The moment the value is first pasted into chat.** By then it has been sent to the model provider and stays in Hermes's session log (`~/.hermes/state.db` and others) and on the chat platform, none of which nyx controls. Any secret pasted into chat should be rotated.
- The message on the chat platform after a reveal (Telegram and others keep it). Either delete that message by hand after revealing, or use the command line.
- With 6.3 (via the model): the value goes into the model provider's and Hermes's session logs.
- Other enabled Hermes plugins under 6.2: their `post_llm_call` hooks get the replaced reply (`turn_finalizer.py:485`).
- Group chats. The vault does not tell speakers apart (§2): with the bot in a group, anyone in the group counts as the owner, and revealed values are posted to the group. With the vault on, keep the bot out of groups.
- The owner's chat account being stolen.
- The machine already being compromised, or malware under the same system user: whatever can read the key file can decrypt.
- Someone looking over your shoulder.

## 10. Configuration

| Key (`nyx.json` / environment variable) | Default | Values |
|---|---|---|
| `vault` / `NYX_VAULT` | `off` | `off` / `on` |
| `vault_reveal` / `NYX_VAULT_REVEAL` | `direct` | `direct` (6.2; points to the command line without the plugin) / `cli` (the tool can list but not reveal) / `llm` (6.3) |
| `vault_kinds` / `NYX_VAULT_KINDS` | `all` | `all` / `tokens` (do not keep passwords) |
| `NYX_VAULT_BACKEND` | `auto` | `auto` / `file` / `keyring` |
| `NYX_VAULT_KEY` | none | base64-encoded master key; wins over the key file |

`vault`, `vault_reveal` and `vault_kinds` appear in `hermes memory setup`. When an environment variable is also set for the same item, the environment variable wins.

## 11. Files changed

| File | Change |
|---|---|
| `core/secret_vault.py` (new) | Two backends, encryption, dedup, reference state, `put / lookup / entries / get / delete / gc / wipe / rekey / status / health`, audit and rate limits, direct handles, confirmation codes, literal hiding of revealed values |
| `core/secret_filter.py` | `scrub(..., vault=None)`: every complete match calls the callback for an id, and the placeholder gets ` #vN`. The existing `_PLACEHOLDER` regex already matches the numbered form |
| `core/memory_provider.py` | `sync_turn` passes a `vault=` callback for user-side messages; the two tools and the gates; `on_turn_start` checks the confirmation phrase; one system-prompt line; config items; the other write hooks also hide revealed values literally |
| `nexsandglass_hermes_reveal/` (new) | Companion plugin: registers `transform_llm_output` |
| `nexsandglass/vault.py` (new) | The command line of §6.1 |
| `core/erasure.py`, `core/quarantine.py` | Purge follow-through; `wipe_free_space` covers `vault.db` |
| `core/secret_scrub.py`, `scrub.py` | `--to-vault` |
| `core/memid.py` | The `vault` health item |
| `pyproject.toml` | Optional dependencies `vault`, `vault-keyring`; entry point `hermes_agent.plugins` |
| `.github/workflows/*.yml` | CI installs `.[vault]`; the pre-release smoke test checks the `nyx-reveal` entry point |
| `tests/test_secret_vault.py` (new), `tests/conftest.py` | 22 tests; clears `NYX_VAULT*` and points `XDG_CONFIG_HOME` at a temp directory (never touches a developer's real key file) |
| `benchmarks/vault_hermes_e2e.py` (new) | End-to-end acceptance in real Hermes (run by hand before a release; CI does not install hermes-agent) |

## 12. Tests and acceptance

Tokens in the tests are always assembled at runtime, so no complete token appears in the source and GitHub push protection is not triggered (as in v7.14.0).

`tests/test_secret_vault.py` covers:
- Encryption: round trip; dedup; ids never reused; key file 0600 and directory 0700, outside the data directory; ciphertext moved to another row does not decrypt; a wrong key reports "wrong key"; permissions too wide are refused; without a backend, or with a broken cryptography (raising `BaseException`), values are only hidden; the keychain backend only trusts real system keychains.
- Collection: user-side values are kept, assistant replies only reuse existing ids; `<private>` and truncated values are not kept; the `tokens` level keeps no passwords; a numbered placeholder passes through again unchanged; a raw-byte scan of the whole data directory finds no value.
- Gates: no explicit request, subagent, scheduled job, unknown id, source in quarantine, rate limits, lock after 3 wrong confirmation phrases, the model claiming confirmation in its arguments has no effect, Hermes approval approved / denied / turned off (falls back to the confirmation code), a different `rule_key` each time.
- Direct: no value in the tool result; the confirmation code never passes through the model; the hook swaps the handle for the value; other sessions cannot swap it; single use; the value is hidden literally in later `sync_turn` calls.
- Reveal paths: the `cli` level and a missing plugin both return `use_cli`; the `llm` level returns the value, but it does not enter nyx's memory.
- Plugin: it registers `transform_llm_output`, and its source contains no words that would mislead Hermes.
- Lifecycle: not revealable after forgetting, revealable after restoring; `purge` and an immediate purge both remove it; with the same value in two memories, forgetting one does not affect the other.
- Command line: `list` never shows values, `show`, `status`, after `rekey` entries still decrypt and dedup still works, `delete`; doctor has the `vault` item.
- `scrub --to-vault`: a dry run keeps nothing; after `--apply` no value is found in the data directory, the journal and the hub agree, the quarantined entry is not kept; it errors when the vault is off.

Acceptance results:
1. With `NYX_VAULT=off`, only two expected values in existing tests changed (one more config item and one more health item); everything else passed as before.
2. End-to-end acceptance in real Hermes: see §6.2.

## 13. Decisions made (v7.15.0)

1. The default reveal path is `direct`: with the companion plugin installed it goes direct; without it the agent tells the owner the command line, never falling back to the model.
2. Passwords are kept too (`all`). Set `vault_kinds: tokens` to keep only tokens.
3. `scrub --to-vault` is done. Values already erased by `scrub --apply` cannot be recovered.

## 14. Related issues

- `owner_ids` has no effect under Hermes 0.19.0 (see §2); not changed this time.
- ~~"Discovered automatically after pip install", as the README recommended, does not hold under Hermes 0.19.0~~ (fixed in v7.15.0). Hermes 0.19.0 only finds memory providers by directory: first the bundled `plugins/memory/<name>/`, then `$HERMES_HOME/plugins/<name>/` (`find_provider_dir` at `plugins/memory/__init__.py:124`), and it does not scan the `hermes_agent.memory_providers` entry point. Now `python3 -m nexsandglass_hermes install` is used instead:
  - find the Python Hermes uses, and install nyx-memory there if missing;
  - write the entry file in `$HERMES_HOME/plugins/nyx/`;
  - set `memory.provider: nyx`;
  - finally confirm once with Hermes's own loader.

  Regular plugin entry points (`hermes_agent.plugins`, which nyx-reveal uses) are scanned by Hermes and unaffected.
