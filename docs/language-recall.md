# Cross-language evidence retrieval (LANG-04)

Version 7.26.0 includes LANG-04's bounded Chinese/English lexical equivalence
for historical recall, fused with the existing optional semantic channel.
LANG-06 joint acceptance and M2 voice scheduling remain pending.
Original quotes, canonical IDs, source trust and profile selection stay intact.

## Retrieval and evidence boundaries

`language/query.py` builds an ephemeral query plan from the explicit LANG-02
concept aliases and a small set of retrieval topics: allergy, intolerance,
preference, residence, vehicle, employer, email and phone. Topics are lexical
cues, not assertions about a person. A plan does not translate stored text,
rewrite facts, import adapters, change response preferences or grant access.

`core/language_search.py` looks up alternative spellings in the existing
inverted index. It verifies candidate text against the plan and hydrates each
unique live canonical source. Missing, deleted or ambiguous bindings are
excluded; stale postings cannot resurrect replaced original text. No new
authoritative database, embedding requirement or stored-language migration is
introduced. Existing indexes remain readable under their original token policy.

Chinese one-character aliases such as `虾` require special handling because the
legacy index stores Chinese bigrams. A bounded vocabulary pass finds associated
postings, then exact plan verification checks the original evidence. Recognized
compounds and qualifiers do not become their unqualified parent: peanut oil is
not peanuts, raw peanuts are not unqualified peanuts, lobster is not the shrimp
alias, and goldfish is not the food-fish alias. The list and short-word contexts
are finite; unfamiliar compounds can be missed. This is not Chinese word
segmentation or general entity resolution.

When both a concept and a topic are explicit, a candidate must match both facets.
Limited direct owner/family cues prevent recognized scope mismatches; comparison
and ambiguous multi-person queries retain broader evidence. Unknown names or
subjects are not resolved. Negation, uncertainty and qualifications remain in
the complete quote; lexical association does not assert that a health claim is
true or that its subject is the owner.

`SearchRouter` merges the alias candidates into its existing lexical/vector
pool. Reciprocal rank fusion gives the lexical and alias routes weight 1 and
the semantic route its existing default weight 0.5. Complete exact lexical
matches retain priority. The configured semantic score/background thresholds
are unchanged. Alias lookup failures preserve the original search routes.
Applications can explicitly use `SearchRouter(language_search=None)` to disable
this added route; `vector=None` disables only the semantic route.

Known typed conflicts also veto a semantic contribution: similarity cannot
equate milk with lactose, a qualified allergen with its bare parent, preference
with an explicit allergy question, or recognized different subjects. This guard
uses current canonical text when it exists. Legacy unbound vector adapters keep
their old fallback; M1 typed export still rejects unresolved journal bindings.
Absence of a recognized cue is not a semantic veto or evidence of absence.

Before host injection, `relevance.keep()` recognizes the same bounded lexical
equivalence and checks typed conflicts on semantic hits. The existing trust,
tombstone, request deadline and final complete-evidence budget gates still
apply. The new route does not bypass M1 correlation checks or turn an unverified
source into a trusted fact. The host hydrates and budgets complete original
records, including conditions or corrections that a short snippet might omit.

## Bounds and readiness

The deterministic route has explicit work limits: 2,000 query characters, 8,000
evidence characters for matching, 12 recognized query terms, 96 posting tokens,
50,000 vocabulary entries for single-character lookup, 2,000 visited posting
references and 120 candidates. Oversized/qualified queries can retain only the
legacy routes. These limits constrain matching work, not the original evidence
stored or exported through the shared budget.

On a direct router instance, `router.language_search.status` distinguishes
`no_terms`, `ready`, `bounded` and `unavailable`. `bounded` means that this route
did not search all possible candidates; an empty list is not proof of no memory.
The existing `semantic.stats()` reports enabled/backend/ready, pending vectors,
configuration problems and failures. Neither successful alias retrieval nor a
provider label proves that a semantic model was ready. The typed packet does
not add a new backend-status field in this increment.

No-vector mode provides these known aliases and topics; it cannot translate
arbitrary descriptions. Model loading and vector indexing remain asynchronous
in normal operation. Raw acknowledgement does not wait for a model. The
benchmark deliberately waits for a real backend to be ready before measuring
its results; that is an evaluation procedure, not the runtime write policy.

## Frozen results

The 28-case corpus was frozen before product changes in `b02a5ff`, SHA256
`c036fe34f429755b302dae388416c87af3d2bf1e69b8f490e43b724275423a0f`.
Cases contain distractors and grouped development/heldout assignments. They
remain implementer-authored synthetic regression cases, not an independently
authored blind benchmark or open-domain language-quality proof.

| Configuration | Alias target evidence | Structured-topic target evidence | Open paraphrase target evidence | Specified negative records returned |
| --- | --- | --- | --- | --- |
| No vectors, before → after | 0/12 → 12/12 | 0/4 → 4/4 | 0/8 → 0/8 | 0 → 0 |
| Real local model, before → after | 9/12 → 12/12 | 4/4 → 4/4 | 3/8 → 3/8 | 2 → 0 |

The real model is
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, pinned revision
`e8f8c211226b894fcb81acc59f3b34ba3efd5f42`: CPU, 384 dimensions, Torch
2.6.0+cpu, sentence-transformers 3.4.1, transformers 4.49.0. The same weights,
dependencies and default thresholds were used before and after. No weights were
trained or bundled into Nyx. Model files were fetched separately; all measured
embedding inference ran locally with offline flags. No external inference or
answer-model calls were made. Paid/external API quality was not evaluated;
existing API protocol/configuration and failure tests remain separate evidence.

The model's 3/8 open-case result remains a concrete limitation. This increment
does not claim improved open paraphrase quality, cross-language equivalence for
all entities or perfect precision. The `extra` field counts returned records
outside each case's specified target set, not human-judged irrelevance. For
example, local alias cases retain 11 extra records and open cases retain 15;
eliminating two named negative controls is not elimination of every false hit.
Per-case write/recall timing and model-load duration are diagnostics from this
cloud machine, not a controlled latency comparison or a service guarantee.

The original frozen 60-case corpus separately improves historical evidence
visibility from 2/12 to 10/12 in each cross-language direction and from 10/12 to
12/12 in each same-language direction; mixed input remains 12/12. Current
profile evidence remains 12/12 per direction with zero stale preferences and
4/4 known alias pairs. Unknown-object cross-language questions remain missed in
no-vector mode. The LANG-03 corpus retains 8/8 time and 9/9 profile/event checks.

Frozen source commits, hashes, individual outcomes and timings are recorded in:

- [No-vector baseline](../benchmarks/results/language_recall_v1_before_off.json)
  and [LANG-04 no-vector](../benchmarks/results/language_recall_v1_after_off.json).
- [Real-local baseline](../benchmarks/results/language_recall_v1_before_local.json)
  and [LANG-04 real-local](../benchmarks/results/language_recall_v1_after_local.json).
- [Original corpus after LANG-04](../benchmarks/results/language_v1_lang04_off.json)
  and [LANG-03 regression](../benchmarks/results/language_time_v1_lang04.json).

The baseline uses clean `b02a5ff`; after-change reports use clean product commit
`d3f876ed20747e5f43863317bdf9272edcc70b50`. Unit/integration tests, package
checks and external CI are independently recorded in [PLAN.md](../PLAN.md).
No user's server, real audio or model answer utilization was tested.

```sh
python benchmarks/language_recall_eval.py --backend off --json /tmp/recall-off.json
# Download/pin a local model separately; this runner does not select an API.
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python benchmarks/language_recall_eval.py \
  --backend local --model /path/to/pinned/model \
  --model-revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42 \
  --json /tmp/recall-local.json
```

LANG-05 now adds [presentation separation](language-presentation.md); LANG-06's common gate remains next. Broader
backend selection, calibration and independently authored language evaluations
remain necessary before treating open cross-language recall as equivalent.
