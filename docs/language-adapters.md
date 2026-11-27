# Language adapter foundation (LANG-00 / LANG-02, with LANG-03 integration)

This branch builds on released M1 / 7.25.0. It is not a new public release and
has not completed the entire LANG workstream. LANG-01 remains the optional
input/response/locale/timezone contract; these adapters handle language-specific
rules without making that metadata an authority or store selector.

## Implemented boundary

`nexsandglass.language` provides an explicit `RuleAdapter` protocol and the
existing Chinese/English rule backend. Profile classification and topic slots
now live in `language/bilingual.py`; segmentation has explicit profile/fact
policies preserving previous evidence units. `understand.extract_rules()` goes
through the adapter boundary and reuses the established parser, temporal policy
and optional upstream LLM integration. LANG-03 applies source assertion/time guards to both rule and LLM candidates. This is not a parallel retrieval engine.

```python
from nexsandglass.language import analyze, entity_key

analysis = analyze('我喜欢红茶。', language='zh')
assert analysis.keys[0].predicate == 'likes'
assert analysis.keys[0].object == 'drink.black_tea'
assert entity_key('black tea', 'preference') == entity_key('红茶', 'preference')
```

An application can supply trusted adapter code to `analyze(..., adapter=...)`,
`understand.extract_rules(..., adapter=...)`, or the pure profile helpers.
No adapter is imported from query text, a session ID or an untrusted plugin name.
Persisted profile views use the bundled adapter and its versioned policy; custom
pure helper calls do not silently change a store's projection configuration.

Declared unsupported languages produce `unsupported_language`; an unmatched
sentence produces `no_match`. Neither response guesses that no fact exists.
The caller's raw write remains authoritative. With no declared language the
legacy bilingual rules still run opportunistically, without claiming full
language identification. `NYX_LANG` continues to control interface wording;
this layer does not update a user's response preference.

## Stable labels and original evidence

The ontology defines stable predicate labels and exact, typed entity aliases.
For example `住在` and `lives_in` share a predicate key; the legacy vehicle label
`使用` / `uses` maps to the narrower semantic name `uses_vehicle`. Unknown custom
relations remain custom. `Fact.semantic_key` is an additive property; existing
positive Fact values and `to_dict()` exports retain their shape. LANG-03 adds explicit `not:` relations and polarity for denials; see [time and assertion policy](language-time.md).

Concept keys are comparison hints, not canonical memory IDs, global entity IDs
or authority to merge records. Subject identity stays separate. The same word
under another subject or predicate does not become the same fact. Original
objects, quotes, source trust, timestamps, `mem_id` and tombstones are retained.
Keys hide their potentially private literals in `repr`; they are not added to
M1's content-free host traces.

The version-1 alias table is deliberately explicit. It includes common food
aliases, selected drinks/activities and vehicle brands, with exact normalized
matching. It does not equate milk with lactose, wheat with gluten, peanuts with
tree nuts, fish with shellfish, or tea with every subtype. Modifiers such as
`raw peanuts`, `peanut oil`, and vehicle model names do not disappear. There is
no fuzzy similarity, model-generated synonym list or general entity resolution.
Unknown literals remain typed literals; unsupported aliases can be missed.

Graph predicate filters accept stable, English tool and legacy Chinese labels
at read time, without rewriting historical rows or IDs. New explicit graph
writes use the existing Chinese storage spelling. Historical contradictions already stored under different relation spellings are not silently merged. LANG-03 adds source-bound preference/denial selection; it does not perform universal historical reconciliation.

## Profile comparison policy

Equivalent known preference objects now share a topic slot. For example, an
explicit newer `I no longer like black tea` can replace the older current-view
entry `我喜欢喝红茶`; both original records remain searchable. This fixes topic
identity for the existing recency policy, not all forms of cross-language
negation, cancellation, conditional preference or temporal correction.

Topic identity alone is insufficient to discard a health constraint. Qualified,
uncertain or compound allergy statements are retained in full rather than
partially superseded by a matching alias. Family-member and untrusted statements
cannot replace the owner's preference. The bounded LANG-03 polarity, time, locale and condition policy is now documented in [language-time.md](language-time.md).

LANG-02 changed the profile processor from version 4 to 5; LANG-03 advances it to 6 for the additional assertion/time policy. M1's durable job protocol
rebuilds only that derived view and checkpoints it atomically. It does not
rewrite raw records, IDs or source bindings. Future rule/alias/normalization
changes affecting the view must bump its processor version again.

## Frozen baseline and results

The corpus was committed before product changes in `134f7c1`; M1 product code
was unchanged from `d78016d`. Its SHA256 is
`2c8f9a8f2fd39ac81e7febee3fd0db8595792115c4eeae81e752913b6a4efd49`.
It has 12 persona/event groups and 60 cases covering four Chinese/English
write/query directions and genuinely mixed records/queries. Translations and
related persona cases do not cross the development/heldout group assignment.

These are transparent synthetic regression fixtures written by the implementer,
with shared templates. The grouped heldout split is not an independently authored
blind benchmark and does not establish open-domain generalization. Baseline
results cover M1 before adapter changes; its `code_dirty=true` records the then
uncommitted benchmark harness, not product edits. The freeze commit reproduces
the same M1 product tree. After-change evaluation was run against clean product
commit `96654ffb05d1057f83b11a571c79fb14166c5612` with the identical corpus.

| Write → query | M1 final historical evidence | LANG-02 final historical evidence | Stale current-view entries before → after |
| --- | --- | --- | --- |
| Chinese → Chinese | 10/12 | 10/12 | 2 → 0 |
| English → English | 10/12 | 10/12 | 2 → 0 |
| Chinese → English | 2/12 | 2/12 | 2 → 0 |
| English → Chinese | 2/12 | 2/12 | 2 → 0 |
| Mixed → mixed | 12/12 | 12/12 | 2 → 0 |

Expected profile evidence is 12/12 in each direction before and after. The four
explicit alias-slot pairs improve from 0/4 to 4/4. Repeated language variants
are correlated; these counts are not independent real users. The runner records
profile visibility, source-bound full-evidence recall, classification and
extraction counts separately. Extraction counts are diagnostics, not precision.
No answer model is called, and no answer-use/QA accuracy is reported. Embeddings
and external models are disabled. Historical recall is unchanged in this step;
LANG-04 must address it explicitly rather than report profile carriage as
cross-language retrieval improvement.

Reproduce from the repository:

```sh
python benchmarks/language_eval.py --corpus benchmarks/fixtures/language_v1.json \
  --split all --json /tmp/language-results.json
```

Frozen reports: [M1 baseline](../benchmarks/results/language_v1_m1_baseline.json)
and [LANG-02](../benchmarks/results/language_v1_lang02.json). Each contains corpus
hash, source commit, backend mode, split and per-case results. Every case runs
in a temporary explicit store without a credential getter.

Validation: 41 new tests cover typed aliases, qualifiers, subject/trust separation,
custom adapter selection, unchanged exports and original evidence, read-only
legacy graph compatibility, projection rebuild, store isolation and the real
write/recall/forget/restore path. Python 3.12 and 3.13 full suites each passed
1,244 tests with 2 configured skips, including actual Hermes source integration.
Separate Conduit HTTP and package-install checks are recorded in PLAN.md.

LANG-03 now adds [temporal/negation policy](language-time.md), preserving the original corpus results in a [separate report](../benchmarks/results/language_v1_lang03.json). LANG-04 now adds [bounded historical retrieval fusion](language-recall.md) with separate real-model results. Next: LANG-05 presentation, then LANG-06's shared gate. M2 remains behind that gate.

LANG-04 results supersede the historical-recall status above for the current branch: the same 60-case no-vector corpus now reaches 10/12 in each cross-language direction. The [retrieval documentation](language-recall.md) separates known lexical equivalence from the real model's remaining open-paraphrase failures.
