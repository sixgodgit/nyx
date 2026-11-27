**M1-02 — complete final-context accounting**

The shared composer selects indivisible context items under one declared budget.
It counts the complete final text, including headings, prefixes, dates/source
labels supplied by the caller, separators and suffixes. No item is shortened to
fit. The default counter preserves the existing Nyx estimate; it is not a model
tokenizer. A caller may supply a deterministic tokenizer-backed counter.

```python
from nexsandglass.runtime import ContextItem, ContextSection, compose_context

sections = (
    ContextSection(
        "constraints", "[Current applicable constraints]", priority=100,
        items=(ContextItem("allergy", "Do NOT serve peanuts.", required=True),),
    ),
    ContextSection(
        "corrections", "[Current corrections]", priority=90,
        items=(ContextItem("address", "The current address is Shanghai, not Hangzhou."),),
    ),
    ContextSection(
        "task", "[Current task]", priority=80,
        items=(ContextItem("meal", "Plan tonight's dinner."),),
    ),
    ContextSection(
        "followups", "[Due follow-ups]", priority=70,
        items=(ContextItem("interview", "The interview is tomorrow."),),
    ),
)
result = compose_context(sections, token_budget=200)
if result.required_omitted:
    # Ask for more context capacity or handle the missing constraints explicitly.
    # Do not silently use ordinary advice after dropping a required constraint.
    context_unavailable = True
else:
    context_text = result.text
```

These literals illustrate composition, not automatic fact extraction. The host
must load eligible current profile/task/evidence data in its authorized scope.
The composer does not authenticate sources, infer medical truth, classify a
sentence as critical, or grant instruction authority. Explicit priorities and
requirements are caller policy; text cannot self-declare its own authority.

**Selection contract**

- Required items are evaluated together, with all their framing. If they cannot
  fit, or a required item is blank, the entire output is empty and omissions are
  reported. No ordinary item is used as a fallback for missing required context.
- Zero budget always emits empty text, even when the legacy estimator rounds a
  few ASCII characters to zero tokens.
- Optional selection uses descending section priority, descending item priority,
  and stable input position. Display order remains input order. This is a
  deterministic greedy policy, not a global optimizer.
- The counter sees a complete proposed final render for every admission. It must
  be deterministic, count empty text as zero, and return nonnegative integers.
- Heading and suffix stay attached to the selected items of their section. An
  earlier/replaced fact should be supplied in an explicitly historical section,
  so its status cannot be separated from its text by budget trimming.
- Related statements that must remain together, including their correction or
  negation, belong in one ContextItem. The composer cannot recover relationships
  or words already removed by an upstream extractor.
- Selected EvidenceRefs are copied in final display order. Omitted items do not
  contribute evidence. IDs in selected/omitted/required_omitted identify input
  items without copying their omitted payload into diagnostics.
- The immutable result records counter_name, token_budget and est_tokens.
  Custom tokenization and the default estimate must be reported separately.

Candidate pools must be bounded by their caller. Whole-render accounting does
not itself impose an input-size, CPU or I/O deadline, and custom counters may be
expensive. The composer performs no retrieval, model calls, network or writes.

**MemoryBundle adoption**

`build_bundle()` and `ContextBuilder.build_bundle()` retain their existing
arguments and accept optional `sections=()` and `counter=None`. Supplemental
sections can supply profile, corrections, follow-ups and task data alongside
retrieved memories, under the same final budget. The `bundle.*` section-key
namespace is reserved for the legacy slots.

The builder now:

- Counts all rendered slot headings, diagnostics and persona/offset/thread/task
  layers, including the no-candidate case.
- Preserves actual MemoryObjects when available, including type, origin, trust,
  validity and source identity. Legacy string-only contexts retain a fallback.
- Keeps unverified objects in their labelled section and never copies them to
  an unqualified contradiction diagnostic. Legacy Memory conversions retain
  their known trust signal. Caller-supplied supplemental sections still require
  caller-established source eligibility.
- Keeps contradiction text whole, with diagnostics below evidence in priority;
  summaries are computed from retained memory objects rather than all inputs.
- Exposes selected_objects, composition, omitted_items and required_omitted.
  Omitted source payloads and empty supplemental headings are removed from the
  retained slots/private lookup maps as well as from rendered text.

Every render updates final accounting and retained metadata. Bundles are
selected snapshots: increasing capacity, restoring omitted inputs or changing
the input data requires building a new bundle from the source candidates.
Rendering with a larger separator may drop more items; it cannot reintroduce
previously omitted ones. A failed required group remains unavailable.

CompositionResult.evidence covers references supplied on ContextItems; legacy
MemoryObjects remain available via selected_objects. This increment does not
invent canonical bindings for every legacy projection or replace the M1-01
ContextPacket transport with an automatic export of an entire mutable bundle.

**Hermes helper adoption and remaining boundaries**

The per-turn `_budget_memory_items` and `_budget_blocks` helpers now use the
shared composer. Date/header overhead remains included. `_memory_line` preserves
the complete supplied record rather than taking its first 300 characters; a
long record fits whole or is omitted when a budget is supplied. The unbudgeted
`memory_block()` remains a formatter, so its caller must select/budget inputs.

M1-02 alone did not establish a shared quota across separate system-prompt and
prefetch calls. M1-03 now coordinates these adapters, removes the profile and
follow-up character-prefix limits, and uses whole canonical results for MCP.
Voice startup retains its dedicated profile/follow-up view and 4,000-character
limit. The exact quota scope, reservation lifetime, retained extraction limits
and generated/sent/applied boundaries are documented in
[host adapters](host-context.md). This does not claim that every original record
is shown whole on every path: personal projections are still extracted views,
and oversized indivisible evidence can be omitted.

No storage format, retrieval backend, base-record write path or language
understanding policy changes here. Chinese/English/mixed-text tests verify
accounting and input preservation, not cross-language semantic equivalence.
