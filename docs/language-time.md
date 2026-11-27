# Time frames and assertion corrections (LANG-03)

Version 7.26.0 includes LANG-03's bounded Chinese/English time and correction
rules. This is not a general temporal NLP system or completion of the LANG
workstream. Original evidence, canonical IDs, tombstones and source bindings remain
authoritative. Language metadata does not grant authority or select a store.

## Observation frame and replay

New callers can bind an original observation instant and independent language
metadata:

```python
from datetime import datetime, timezone
from nexsandglass.runtime import observe, LanguageContext

observe(
    "我明天有面试。", source="user",
    observed_at=datetime(2026, 3, 1, 23, 30, tzinfo=timezone.utc),
    language=LanguageContext(input_language="zh", locale="en-GB",
                             timezone="Asia/Shanghai"),
)
```

In this example the local observation date is March 2, so tomorrow is March 3.
`observed_at` must be aware. Optional Hermes profile `locale` / `timezone`
configuration supplies a frame for new writes; input language and response
preference remain separate. The profile settings use `NYX_LOCALE` /
`NYX_TIMEZONE` internally. Validation precedes configuration persistence.

The additive `observation_context` table stores the reference instant, locale,
timezone, basis and declared input language in the same transaction as canonical
allocation and provenance. A failed allocation rolls back its frame too.
Projection jobs are invalidated transactionally if this metadata changes.
Workers use each record's stored frame rather than their current clock or
current profile configuration. Committed projection batches continue immediately;
only stalled checkpoints back off. Voice readiness retains its one-second limit
and never treats a partial view as complete. Profile processor v6 and follow-up processor v2
rebuild derived views through M1's durable job protocol.

Records without a frame retain their legacy wall-clock interpretation. Current
settings do not retrofit an original timezone or restamp old records. Historical
imports preserve their known epoch instant but, by default, record an unknown
original local frame (`historical_import_unknown_frame`). An epoch alone does
not reveal what “tomorrow” meant to the speaker. `history_import.write_turns`
accepts optional `language_context` only when the caller knows that original
frame; unresolved relative plans otherwise remain raw evidence.

## Deterministic time policy

`nexsandglass.language.time.resolve_time(text, reference=..., language=...)`
returns a `TimeResolution` with status, source span, policy version, parsing
basis, date bounds, optional UTC instant and ambiguity candidates. `scan_times`
provides individual matches; the single-resolution helper rejects multiple
competing dates. Source text is never translated or replaced.

- Supported explicit forms include ISO dates, Chinese year/month/day and
  numeric day/month/year or month/day/year. Ambiguous numeric order requires a
  recognized locale: MDY for `en-US` / `en-PH`; DMY for `en-GB`, `en-AU`, `en-NZ`,
  `de-DE`, `fr-FR`, `es-ES`, `it-IT`. Other or absent locales retain ambiguity.
- Relative days, `this` / `next` weekdays, Chinese week forms and bounded
  day/week/month/year arithmetic use the observation frame. `next Wednesday`
  means Wednesday in the next ISO week. Calendar month arithmetic clamps to
  the last valid day rather than treating every month as 30 days.
- Optional `HH:MM` / `am` / `pm` clocks require a known timezone. IANA timezone
  round trips distinguish resolved instants, nonexistent spring-transition
  times and ambiguous autumn-transition times. Neither DST fold is guessed.
- Explicit modern instants without an original timezone return
  `needs_timezone` for relative days or local clock interpretation. Unknown,
  invalid or ambiguous values do not become invented UTC facts. Legacy callers
  without metadata retain existing date-only compatibility.

This grammar has finite coverage. It does not parse every colloquial expression,
all locales, arbitrary event coreference or every historical interval. Raw
records remain available when structured interpretation is deferred.

## Assertions, denials and current views

Recognized questions, conditional or uncertain assertions, ambiguous double
negation and historical preferences without a reliable current interval are
deferred for current fact extraction. Qualified or uncertain health statements
retain their complete evidence under the existing profile policy. Optional LLM
extraction cannot bypass the source assertion/time guards.

Explicit denials carry `polarity=-1`. Fact exports and stored graph relations
use a `not:` prefix as well, so a reader that drops new fields still cannot
mistake the relation for an affirmative claim. Graph read rows expose the
underlying `base_relation`; the provider tool translates that base label and
returns, for example, `relation="not:likes", base_relation="likes", polarity=-1`.
Positive legacy exports keep their previous shape.

Current/as-of graph views compare typed preference objects and preserve the
latest source-bound assertion, including denial and later reassertion. Explicit
scalar denials mask only their matching object. History/known-at views retain
versions; conflicting claims from the same source remain flagged. Out-of-order
source ingestion does not make an older utterance current. Existing scalar
interval/closure policy is retained; this does not reconcile all historical
graph contradictions or migrate ledger authority.

Source eligibility remains independent: known canonical facts used in current
views must be active, trusted user evidence. Forget/restore changes eligibility;
other profiles and external/assistant statements cannot overwrite the owner.
Legacy manual graph rows without canonical source IDs retain compatibility.

## Event cancellation and rescheduling

Follow-ups associate recognized subjects, exact named anchors and explicit dates.
Known company/location aliases and bounded proper-name rules help distinguish
events; they are not global entity identity. A Tencent interview cancellation
does not cancel a Google interview or a family member's appointment. A target
must be unique. An unanchored cancellation matching several plans leaves them
unchanged and records ambiguous binding state instead of cancelling them all.
Rescheduling uses the final target date rather than its earlier “from” date.

Only trusted user evidence can cancel a plan. Assistant text may suppress a
question already asked, but cannot cancel the user's event. Cancellation is
reversible when its source is forgotten/restored. Candidate processing is
bounded; an oversized later-record window raises a readiness timeout rather
than reporting a complete result after silently truncating it.

## Frozen evidence and limits

The 17 synthetic cases were frozen before product changes in `530dcd0`:
corpus SHA256 `38467996e0cad233c5df4e6920de9fe0b51d7653c303d7087a1953586f46d329`.
The [baseline](../benchmarks/results/language_time_v1_baseline.json) at LANG-02
passed 2/9 existing profile/event cases. The
[LANG-03 report](../benchmarks/results/language_time_v1_lang03.json) passes 9/9;
the new time API separately passes 8/8 conformance cases, without a pre-change
API counterpart. Clean evaluation source is
`e00f471aaba46cdc61b91ee5723f90e3d96393ce`.

The [original 60-case corpus](../benchmarks/results/language_v1_lang03.json)
retains LANG-02's results: current profile evidence 12/12 in each direction,
zero stale preferences, four alias pairs matched, historical same-language
recall 10/12, mixed recall 12/12 and cross-language recall 2/12 each direction.
LANG-04 remains responsible for retrieval fusion.

These implementer-authored fixtures are regression/conformance checks, not
independent blind or open-domain quality evidence. No answer model, real audio
or user server was used. Actual Hermes and Conduit source integration, package
installation and CI are recorded separately in [PLAN.md](../PLAN.md).

```sh
python benchmarks/language_time_eval.py --json /tmp/language-time.json
python benchmarks/language_eval.py --corpus benchmarks/fixtures/language_v1.json \
  --split all --json /tmp/language-original.json
```
