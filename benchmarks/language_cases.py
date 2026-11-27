"""Frozen synthetic language fixtures, grouped by persona/event before changes.

Translations of one scenario stay in the same split. This is a transparent
regression corpus, not a blind human-authored language benchmark.
"""
from __future__ import annotations

SCHEMA_VERSION = 1


def corpus():
    groups = []
    # Group assignments are explicit and stable. No random question-level split.
    for split, prefix, allergen, en_allergen, drink, en_drink in (
        ("dev", "lin", "花生", "peanuts", "红茶", "black tea"),
        ("heldout", "morgan", "虾", "shrimp", "绿茶", "green tea"),
    ):
        specs = [
            ("allergy", [f"我对{allergen}过敏。"], [f"I am allergic to {en_allergen}."],
             f"我对哪种食物过敏？{allergen}可以吃吗？", f"Am I allergic to {en_allergen}?", [0], []),
            ("preference", [f"我喜欢喝{drink}。"], [f"I like {en_drink}."],
             f"我喜欢喝{drink}吗？", f"Do I like {en_drink}?", [0], []),
            ("correction", [f"我喜欢喝{drink}。", f"I no longer like {en_drink}."],
             [f"I like {en_drink}.", f"我不再喜欢喝{drink}。"],
             f"我现在还喜欢{drink}吗？", f"Do I still like {en_drink}?", [1], [0]),
            ("subject", [f"我对{allergen}过敏。", f"我女儿喜欢{allergen}。"],
             [f"I am allergic to {en_allergen}.", f"My daughter likes {en_allergen}."],
             f"我和女儿对{allergen}有什么不同？", f"What did I say about {en_allergen} and my daughter?", [0, 1], []),
            ("negation", [f"我对{allergen}过敏，但是不过敏的食物不能据此推断。"],
             [f"I am allergic to {en_allergen}, but do not infer allergies to other foods."],
             f"我的{allergen}过敏有哪些限制？", f"What are the limits of my {en_allergen} allergy statement?", [0], []),
            ("unknown", ["我把钴蓝杯子放在三角架下面。"],
             ["I left the cobalt mug under the tripod."],
             "钴蓝杯子在哪里？", "Where is the cobalt mug?", [], []),
        ]
        for scenario, zh, en, qzh, qen, expected, forbidden in specs:
            group = f"{prefix}-{scenario}"
            variants = []
            for source_lang, records in (("zh", zh), ("en", en)):
                for query_lang, query in (("zh", qzh), ("en", qen)):
                    variants.append({"id": f"{group}-{source_lang}-{query_lang}",
                                     "source_language": source_lang, "query_language": query_lang,
                                     "records": [{"text": text, "source": "user"} for text in records],
                                     "query": query, "expected_recall": [len(records) - 1],
                                     "expected_profile": expected, "forbidden_profile": forbidden})
            # Mixed inputs are distinct from monolingual direction checks.
            variants.append({"id": group + "-mixed", "source_language": "mixed", "query_language": "mixed",
                             "records": [{"text": z + " " + e, "source": "user"} for z, e in zip(zh, en)],
                             "query": qzh + " " + qen,
                             "expected_recall": [len(zh) - 1], "expected_profile": expected,
                             "forbidden_profile": forbidden})
            kinds = {"allergy": ["health"], "preference": ["likes"], "correction": ["likes", "likes"],
                     "subject": ["health", "people"], "negation": ["health"], "unknown": [None]}[scenario]
            for variant in variants:
                for record, kind in zip(variant["records"], kinds):
                    record["expected_kind"] = kind
            pairs = []
            if scenario in ("allergy", "preference"):
                pairs = [{"kind": kinds[0], "a": zh[0], "b": en[0], "equivalent": True}]
            groups.append({"group_id": group, "split": split, "scenario": scenario,
                           "cases": variants, "slot_pairs": pairs})
    return {"schema_version": SCHEMA_VERSION, "groups": groups}
