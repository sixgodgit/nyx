**LANG-01 — optional language context for typed recall**

`LanguageContext` is immutable request metadata. It separates the language of
the current input, an explicitly selected response language, regional formatting
and timezone. It does not detect language, translate evidence, parse relative
dates or save a preference. Those behaviors live outside the metadata contract; the language branch now implements bounded LANG-03 time interpretation separately.

```python
from nexsandglass.runtime.contracts import LanguageContext, RecallRequest

language = LanguageContext(
    input_language="en",
    input_basis="detected",
    response_language="zh",
    response_basis="user",
    locale="en-GB",
    timezone="Europe/Berlin",
)
request = RecallRequest("Where did I leave the blue cup?", language=language)
```

The English input above leaves the declared Chinese response preference intact.
Constructing or passing this object changes neither `NYX_LANG` nor stored user
profile facts. A host may use the metadata when preparing its own answer; the
current recall engine still returns original evidence through its existing
retrieval paths.

| Field | Accepted values and meaning |
| --- | --- |
| `input_language` | A bounded BCP-47-style tag, or `mixed` / `unknown`; default `unknown` |
| `input_basis` | `unspecified`, `detected`, `user` or `configuration`; default `unspecified` |
| `response_language` | A bounded BCP-47-style tag or `None`; never inferred from the input |
| `response_basis` | `unspecified` when no response language is set; otherwise `user` or `configuration` |
| `locale` | An independent BCP-47-style regional tag or `None` |
| `timezone` | An available `zoneinfo.ZoneInfo` key, such as `Europe/Berlin`, `Asia/Shanghai` or `UTC`, or `None` |

Tags are normalized to lowercase for case-insensitive comparison. The syntax
check accepts a 2–8-letter primary tag and 1–8-character alphanumeric subtags,
up to 128 characters total; it is not an IANA language-registry lookup or a full
BCP-47 parser. Legacy locale strings such as `en_US.UTF-8`, free-text instructions
and control characters are not accepted. `mixed` represents multiple languages
without inventing a dominant one; `unknown` represents unavailable input metadata.
Neither is a response-language preference. `auto` is not a language tag here.

Timezone keys retain their IANA spelling and case. The host must have a timezone
database available to stdlib `zoneinfo` when it supplies one; a missing or invalid
key is rejected, never silently replaced with UTC. This contract carries the
timezone without claiming to interpret natural-language dates or daylight-saving
ambiguities.

**Compatibility and invalidation**

- `RecallRequest.language` and `ContextPacket.language` are optional and default
  to `None`. Existing positional arguments, legacy `recall(str)` behavior and
  missing-language callers remain unchanged.
- `packet_for()` copies the language object alongside the other correlation
  fields. `matches_request()` / `can_apply_to()` reject a packet when its language
  context differs from the host's current request, even if IDs were accidentally
  reused. A language/locale/timezone change therefore needs a fresh request or
  revision in a host's scheduling policy.
- A packet without language metadata omits the `language` key from `to_dict()`,
  retaining its original export shape and schema version 1. An opt-in packet
  exports a detached nested dictionary. Neither query nor task text is copied.
- Both fields require a `LanguageContext` instance, not an arbitrary dictionary
  or free-text label. Basis fields use fixed enums rather than original quotes,
  model prompts or credential-bearing evidence strings.

**Authority boundary**

These fields are caller-supplied metadata. `response_basis="user"` documents
what the host reports; it is not proof that a user authorized an instruction.
Language never selects a data directory, grants profile access, changes source
trust or rewrites canonical IDs. The host must bind storage and permissions
through its independent store context and must validate any persistent preference
through the normal source/trust path.

The LANG-01 contract itself adds no cross-language aliases or semantic retrieval guarantees.
The language branch adds a separate [LANG-02 adapter foundation](language-adapters.md) with explicit typed aliases;
it does not turn those metadata fields into retrieval or authorization rules.
The branch also implements bounded [LANG-03 negation, corrections and observation-time interpretation](language-time.md). Open-ended cross-language recall and the common language gate remain LANG-04–06 work.

Tests in `tests/test_language_context_contracts.py` cover old export compatibility,
input/output separation, immutable/detached data, syntactic and timezone validation,
stale-language rejection and the actual typed evidence path retaining its original
canonical binding and unverified tool provenance.

LANG-03 optionally accepts this same object on `observe(..., language=..., observed_at=...)`. Its input language, locale and timezone can be persisted with a new canonical record for replay; it does not persist the response-language preference as a user fact. Existing records are not reinterpreted using current settings. See [observation frames](language-time.md#observation-frame-and-replay) for import and compatibility boundaries.

[LANG-04 retrieval fusion](language-recall.md) separately uses known aliases and an optional semantic backend; declaring an input language neither enables a model nor guarantees retrieval quality. Original evidence and the input/response-preference boundary remain unchanged.

[LANG-05 presentation](language-presentation.md) now provides explicit `recall_presented()` and host rendering options. Raw typed recall remains compatible. Presentation must match the request's response-language context; original evidence and fact-tool fields are retained. Hermes can separately opt in through `response_language`; `lang` remains a UI setting.
