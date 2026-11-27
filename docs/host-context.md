**M1-03 — host adapters and generation traces**

Hermes' provider hooks and the MCP `memory_recall` tool keep their existing
signatures. They now use `RecallRequest`, post-gate `EvidenceRef` bindings and
the shared whole-item composer through `runtime/host_context.py`.

| Path | Input and rendering policy | Generation trace |
| --- | --- | --- |
| Hermes system prompt | Current profile, due follow-ups and existing optional host layers | `hermes_system` |
| Conduit startup | Dedicated current profile/follow-up views; no generic recall or tool instructions | `conduit_voice` |
| Hermes/Conduit recall | Existing recall planner, provenance gate, relevance filter, dates and final framing | `hermes_prefetch` |
| Pre-compression | At most three whole recalled records for the summary prompt | `hermes_compress` |
| MCP `memory_recall` | Existing planner and tool envelope; whole selected result records | Additive `trace` field in the result body |

Profile and follow-up excerpts retain their complete matched sentence rather
than a 160-character prefix. Existing classification, finite view limits,
correction/history handling, owner binding and due-date policies remain. Raw
search/shadow hits are rehydrated from active canonical records before final
selection, so a search backend's 300-character preview cannot lose a later
qualifier. Derived temporal beliefs retain their derived representation.
Canonical IDs are preserved; this does not migrate storage or replace the
retrieval backend. Personal facts recheck current source trust when binding.

The current profile's `health` items form a required group, together with the
vault usage guard when enabled in the system prompt. If the required group
cannot fit, the generated context is empty; ordinary per-turn and compression
recall are suppressed until a subsequent successful snapshot or a genuine new
conversation resets that state. This is a context-selection policy, not a
medical classifier or a guarantee about the host model's eventual behavior.

**Budget scope**

`context_tokens` in `nyx.json`, or the overriding `NYX_CONTEXT_TOKENS` environment
variable, controls an estimated-token envelope. Its default is the system
limit (`NYX_BUNDLE_MAX_TOKENS`, default 1500) plus `prefetch_tokens` (default
600). Zero disables these context hooks, including when saved and reloaded.
Explicit memory tools remain independently callable.

System and voice snapshots each have the system limit. For subsequent recall,
the adapter subtracts the larger returned system/voice reservation from the
total, then applies the per-turn limit. System and voice are alternative startup
surfaces, not two blocks to concatenate in one prompt. Pre-compression applies
the same remaining capacity, with its own 600-token ceiling. Headings, dates,
source labels, separators, notices and other generated text count toward the
budget. All admissions are whole items; a long item is omitted rather than cut.

Reservations retain the largest returned snapshot until reinitialization or
`on_session_switch(reset=True)`. Branch/resume/rewind hooks clear stale traces
but retain reservations because a previous prompt may still exist in the host.
If configuration is lowered below an already returned reservation, future
recall is empty; Nyx cannot retract the earlier snapshot. Configuration changes
that need smaller reservations require rebuilding the host conversation.

The counter is `nyx_estimate_v1`: one token per CJK character plus one per four
other characters, rounded down. It is not model-exact tokenization. The scope
is Nyx's startup snapshot plus the current hook's output, not all conversation
history, earlier prefetch/tool messages, Hermes built-in notes, personality,
provider instructions or the model's entire context window. MCP's budget counts
selected memory text and separators; JSON escaping, response keys, strategy and
trace metadata are outside it. A zero-budget MCP call still returns its control
envelope with an empty result list.

Conduit startup also enforces 4000 characters. Recall on Hermes' `conduit_voice`
platform enforces the memory route's 4000-character result limit before returning,
so an oversized record cannot have its qualifier cut off by that route.
Downstream app briefing limits are separate and remain outside Nyx's control.

**Trace boundary**

`provider.context_trace(channel)` returns a detached diagnostic dictionary;
without a channel it returns the latest trace per channel. It includes request,
session, turn and revision identifiers, declared/used/reserved estimated tokens,
omission counts, required omissions, stable reason codes, selected canonical
evidence IDs and a SHA-256 hash of the generated text. It omits query text,
memory payloads, raw retrieval traces, exceptions and credentials. A mismatched
store returns no traces. Traces are in-memory diagnostics, not a durable audit
ledger or proof of anonymity.

Every adapter trace records `stage="generated"`, `sent=null`, `applied=null`.
The Nyx provider cannot observe the host transport or model application.
`hermes_retrieval` is the intermediate selection trace; `hermes_prefetch` is
the final provider output after notice/transport accounting. Never count an
intermediate candidate, an HTTP success or `provider="nyx"` as model usage.
Session/turn IDs correlate work; they do not authorize a store. Revision defaults
to zero because these hooks do not supply transcription revisions. Latest-turn
scheduling and application acknowledgements remain M2 work.

**Conduit GPT-Live boundary verified on 2026-11-22**

The official notifier incorporated voice facts in
[0.185.0](https://github.com/kaishi00/hermes-conduit-notifier/blob/e08bcb77cc50b4022d8781c39c50dc9b3e5e6b8d/CHANGELOG.md#01850-2026-11-20).
The cross-repository HTTP check passed against official **0.194.5** at
`e08bcb77cc50b4022d8781c39c50dc9b3e5e6b8d`, as well as the existing fork.
The test uses real Hermes/provider modules and isolated local stores, not a live
model, real audio or the user's remote server.

Current upstream app source at
`ea6078cb99ec1befc4c76e96505602396309ae6d` shows:

1. With GPT-Live's **Use Hermes Memory** enabled, `AppState` fetches
   `GET /api/plugins/conduit_push/memory/context` before the call.
2. The notifier reads the active profile's `memory.provider`. An available Nyx
   provider supplies `voice_prompt_block()`; its facts precede built-in notes.
3. `GPTLiveConversationController` includes this text in the call's memory
   briefing. The subscription path has no direct `recall_memory` tool of its
   own; additional work goes through GPT-Live delegation to Hermes.
4. `POST /memory/recall` reaches Nyx's query recall. The integration check
   separately proves retrieval of a historical fixture absent from the startup
   profile, source bindings, omission after forgetting, restoration and no
   journal writes during context reads.

The official notifier falls back to built-in notes if optional voice facts fail,
return a non-string or exceed three seconds. Its response may still name `nyx`
and advertise recall. The check explicitly exercises this fallback, then checks
the positive context against Nyx's exact text hash and canonical evidence IDs.
The fork's older error response remains covered under a separate expectation.

This proves the tested local integration and describes the current upstream
app path. It does not establish automatic retrieval after every utterance or
that the user's installed app/server used a particular memory. Actual deployment
versions, profile configuration and a real phone call remain separate checks.
