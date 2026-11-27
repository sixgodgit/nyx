# Source evidence, facts and presentation (LANG-05)

LANG-05 adds an optional display layer on the language branch. Default
`recall_request()` text, ContextPacket schema version 1 and missing-language
exports remain compatible. Canonical records, typed concept labels, fact-tool
fields and source trust are not localized or rewritten. This is not a release
or an automatic translation engine.

## Independent language choices

`NYX_LANG` / Hermes `lang` continue to select terminal/setup UI wording. Input
language belongs to `LanguageContext.input_language`. The explicit
`response_language` and its user/configuration basis express a host-reported
answer preference; neither input language nor an interface locale creates it.
Language metadata does not authenticate an instruction or select another store.

Raw typed recall remains unchanged even when it carries a response preference.
The new explicit SDK entry point opts into a rendered host context:

```python
from nexsandglass.runtime import LanguageContext, RecallRequest, recall_presented

request = RecallRequest(
    "Am I allergic to peanuts?", token_budget=700,
    language=LanguageContext(input_language="en", input_basis="detected",
                             response_language="zh", response_basis="user"),
)
packet = recall_presented(request, include_dates=True, filter_relevance=True)
```

This goes through the existing source, deletion, scope and relevance gates. It
adds a preferred-answer-language hint and supported display framing, keeping
the evidence units in their original language. It does not change query text,
ranking, canonical IDs, relation keys or user facts.

`render_host_context(..., presentation=PresentationOptions(...))` is the
lower-level option for hosts that already construct gated ContextSections.
Its target must match `request.language.response_language`; missing or different
metadata is rejected. The existing `matches_request()` / `can_apply_to()` checks
therefore reject a packet for a different response-language context. Changed
sidecars or rendering policy also need a fresh request/revision in the host's
scheduling policy. M2's automatic scheduling is not implemented here.

English and simplified Chinese framing are supported. English regional tags
use English labels; `zh`, simplified-Chinese tags and their supported regional
forms use Chinese labels. Other targets, including traditional-Chinese tags,
retain the requested answer-language hint but use English framing and report
`presentation_language_fallback`. This does not claim a translation or the
model's ability to answer in that language. Only exact known framework headings
and follow-up guidance are localized; original item text and custom headings
are retained.

## Cited, host-supplied display translations

Nyx makes no translation or model call. A trusted host may explicitly supply a
`DisplayTranslation` beside an already-gated ContextItem. Each sidecar binds:

- section/item keys;
- the item's exact canonical source-reference ID tuple;
- SHA256 of the original **rendering unit's text**;
- the normalized target-language tag;
- bounded display text.

`unit_digest(item.text)` computes that binding. It is not a replacement for a
canonical `content_hash`: a rendering unit can include an existing date/source
label, a complete excerpt or source-linked derived wording. Hosts must cite the
actual original evidence they intend to translate rather than invent a source
ID or infer a binding from a similar sentence. Unbound generated units cannot
receive a translation. Missing, stale, duplicate, foreign-source or wrong-target
bindings are rejected or omitted, without exporting the rejected text.

An accepted translation is appended to the unchanged original unit and labeled
as **host-supplied, unverified display data**. It is never a replacement quote,
new fact, current belief, instruction authority or stored preference. Source
EvidenceRefs keep their original origin and trust classification. This module
does not verify linguistic equivalence: a host-supplied translation can still
be wrong. The original's negation, uncertainty and qualifications remain
available; Nyx does not claim that the translated wording preserves them.

At most 32 sidecars are accepted per options object, each with at most 8,000
characters. Originals are admitted first under M1's shared budget. A proposed
paired translation is accepted only if recomposing the full output retains
exactly the same admitted source items. It cannot displace another original,
truncate a qualification or create an orphan citation. When it does not fit,
the original remains and `translation_unavailable` records the omission.

The language preamble and all framing/translation text count toward the same
token and optional character caps. A preamble is never returned alone. Required
health context must still fit as complete units; otherwise the existing required
failure policy applies. Expiry during generation clears text/evidence and reports
zero used translations. Rendering does not revoke previously exported content;
hosts must use fresh gated reads after forgetting or source changes.

## Structured fact display

`present_fact(fact, language="zh")` returns a detached `FactPresentation`, not a
new graph row. Its `to_dict()` separates the unchanged `source` export from a
`presentation` sidecar. The sidecar localizes recognized relation wording and
includes original evidence, negative polarity, temporal state and deferred
assertion labeling. Objects, subjects, stored relation spellings and source
quotes remain unchanged. Unknown labels are retained rather than translated
by a model. A pure formatter does not validate the fact's authority; hosts must
use their normal source gates before presenting it as memory.

Changing a display or mutating an exported dictionary cannot update the source
Fact or canonical record. Hermes `nyx_belief` continues to return the existing
English protocol labels, including `not:likes` and polarity, regardless of the
preferred answer language. MCP's existing raw result wire format is unchanged;
hosts can opt into the SDK presentation path separately.

## Hermes / Conduit configuration

The optional Hermes provider setting `response_language` (environment override
`NYX_RESPONSE_LANGUAGE`) is a BCP-47-style tag such as `en` or `zh`. Empty is the
default and preserves legacy output. Configuration validates before saving and
uses existing per-profile settings scope without mutating process environment.
It adds the hint/framework to generated system, voice and per-turn contexts;
it does not save a user-memory instruction or infer a preference from speech.
Actual preference conflicts and answer language remain the host/model's choice.

Existing persistent-context reservations are retained until session
reinitialization. Changing the preference does not retract an already-consumed
snapshot or update a running voice client automatically. A host must refresh
its context and correlation when needed; transport/application acknowledgement
remains outside Nyx's evidence-generation trace.

An opt-in HostTrace has a `presentation` field containing only requested/frame
language, used/omitted translation counts and policy version. Default traces
omit the field. No original or translation text enters it; `text_sha256` covers
the final rendered context. `sent` and `applied` remain unknown.

## Verification and limits

The 12 new-API conformance examples were frozen in `8cefebf`, before product
changes, SHA256 `e8cb1ab48958ce1d4f2f868ace302e3ad445ddb4fbfdb31ec0bb26a9d1c5f617`.
They cover input/response combinations, an unsupported frame, negation,
uncertain health statements and external-source evidence. This is an
implementer-authored API regression corpus, not a translation or model-answer
quality benchmark and not a comparison against a legacy presentation API.

[The report](../benchmarks/results/language_presentation_v1_lang05.json) passes
12/12 from clean product commit
`9c304b1361b387a2677ccfd4fd9b484194ade6c5`. Canonical rows and EvidenceRefs remain
unchanged. [Original language recall](../benchmarks/results/language_v1_lang05_off.json),
[time/correction](../benchmarks/results/language_time_v1_lang05.json) and
[bounded no-vector retrieval](../benchmarks/results/language_recall_v1_lang05_off.json)
retain their LANG-03/04 results. The local model's earlier open-paraphrase 3/8
result is not improved or reclassified by presentation; this step makes no new
model-quality claim.

41 new tests additionally cover invalid bindings, multiple citations, budgets,
required context, expiry before/after composition, frozen fact exports,
preference validation, real parallel profiles, legacy reset and fresh
forget/restore. Full suites, actual Hermes/Conduit HTTP integration, installed
wheel verification and external CI are recorded separately in [PLAN.md](../PLAN.md).
No user server, real audio, external translator or model-answer adherence was
tested. The branch remains unmerged/unreleased; LANG-06 is the next common gate.

```sh
python benchmarks/language_presentation_eval.py --json /tmp/presentation.json
```
