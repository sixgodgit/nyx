**Nyx startup memory for Conduit voice**

Nyx exposes an optional `voice_prompt_block()` capability for voice hosts. It
returns current trusted owner profile facts and due follow-ups without asking a
model to call a memory tool. The matching Conduit notifier change reads this
capability through its existing `/api/plugins/conduit_push/memory/context` route.

This integration requires Nyx 7.24.1 or later and the matching Conduit notifier
support (sixgodgit fork 0.168.9). Updating only one side leaves older behavior.
The client must also request and share memory with the voice session.

**On the Hermes host**

- Configure the active profile to use `memory.provider: nyx` through Hermes's
  memory setup. An MCP tool connection alone does not select the memory provider.
- Keep the profile's Nyx data directory and owner configuration. The integration
  reads that provider; it does not copy memory into a different profile or into
  `MEMORY.md`.
- Use the matching notifier implementation with optional voice-context support.
  Restart the Hermes dashboard/gateway after updating its plugin code.

**On the phone**

In the upstream client source reviewed for this integration, the enabled
GPT-Live settings section contains **Use Hermes memory**. Labels and navigation
can differ by app version. Selecting Nyx under **Memory and delegation** selects
the Hermes backend; it does not by itself prove voice-context sharing is enabled.
If your build does not expose the voice memory option, check its version and
supported voice settings before assuming this integration is active.
After enabling voice memory sharing, end and start the call again. This is an existing opt-in control: the voice
service receives the selected memory. The server change does not turn that
switch on, bypass it, or change the current conversation after it has started.

The existing app already puts the endpoint's `context` text into its startup
briefing, including the ChatGPT subscription path. No new response field or new
app release is required for this startup-context change. A future provider may
choose to support the same optional method; providers without it keep their
existing Conduit behavior.

**Data contract and limits**

```python
def voice_prompt_block(self, *, max_chars: int = 4000) -> str:
    ...
```

- Current profile facts include health/diet constraints, preferences, identity
  and standing requests. Replaced historical preferences are excluded from this
  compact view; they remain available in normal memory/history.
- Due follow-ups respect `follow_ups: off` / `NYX_FOLLOW_UPS=off`. Their general
  behavioral guidance is omitted; the voice host decides how to speak naturally.
- No tool schemas, tool-call directions, L3 stage/persona diagnostics, or empty
  query recall are included. The method does not sync turns or call models or
  network services. Derived local profile indexes may refresh, like normal reads.
- First reads wait for local profile/follow-up indexing through the initial journal
  watermark, sharing a one-second readiness budget. A busy or incomplete index
  reports an error; retry after warm-up and start a new call. SQLite operations
  retain their own timeout and Conduit retains its outer request deadline.
- Nyx returns at most 4,000 characters, keeping complete evidence lines. The
  notifier gives this explicit voice data priority, fills remaining space with
  builtin notes, and keeps its existing overall 8,000-character context cap.
- Reads follow the existing profile, provenance, correction and forgetting
  rules. A later context read reflects forgetting/restoration; an already-open
  remote voice session keeps its previously sent context until it is restarted
  or a future client implements an update/invalidation mechanism.
- Provider calls use the notifier's existing per-profile serialization and
  deadlines. Invalid results and errors are reported through its existing safe
  error responses; they are not turned into leaked backend exception text.

**What this phase changes**

A new call can start with a known allergy, current preferences and an upcoming
appointment already present, without an explicit “call Nyx” request. Tests verify
that the data reaches the startup endpoint without calling `prefetch`,
`system_prompt_block` or `sync_turn` from the read path.

This does not add automatic per-utterance semantic recall or live turn storage.
Those require client transcript events, cancellation/version guards and durable
deduplication with the existing call-end writer. The current ChatGPT subscription
transport cannot be assumed to pause every response until recall finishes.

**Validation**

Nyx unit tests exercise real isolated memories, preference replacement, source
filtering, follow-up settings, complete-line budgets and forgetting/restoration.
The notifier tests cover both old and capable providers, bounded calls, errors,
profile isolation and the unchanged response schema.

An optional cross-repository check loads the real Nyx provider through Hermes
discovery and calls the real Conduit HTTP route in a disposable host:

```sh
CONDUIT_NOTIFIER_SRC=/path/to/hermes-conduit-notifier \
HERMES_AGENT_SRC=/path/to/hermes-agent \
python -m pytest tests/test_conduit_voice_real.py -q
```

It replaces only the host configuration/builtin snapshot reads. It does not use
microphones, OpenAI credentials, a deployed dashboard or an iPhone, and cannot
prove that the spoken model uses every fact correctly.
