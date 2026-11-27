"""Versioned semantic labels; never record IDs, subject resolution or authority.

Aliases are exact and typed. No fuzzy/entity substring matching, stemming of
allergens, parent/child categories or translations from an external model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
import unicodedata

ONTOLOGY_VERSION = "1"
ALIAS_VERSION = "1"

# stable predicate -> (legacy storage spelling, existing tool spelling)
_PREDICATES = {
    "lives_in": ("住在", "lives_in"),
    "uses_vehicle": ("使用", "uses"),
    "employer": ("公司", "employer"),
    "job_title": ("职位", "job_title"),
    "email": ("邮箱", "email"),
    "phone": ("电话", "phone"),
    "likes": ("偏好", "likes"),
    "dislikes": ("反感", "dislikes"),
}
PREDICATES = MappingProxyType(_PREDICATES)


def canonical_predicate(value: str) -> str | None:
    key = str(value or "").strip().casefold()
    return next((p for p, aliases in PREDICATES.items() if key == p or key in aliases), None)


def legacy_predicate(value: str) -> str:
    """Keep historical Chinese storage labels; unknown custom relations survive."""
    p = canonical_predicate(value)
    return PREDICATES[p][0] if p else str(value or "").strip()


def predicate_aliases(value: str) -> tuple[str, ...]:
    p = canonical_predicate(value)
    return tuple(dict.fromkeys((p, *PREDICATES[p]))) if p else (str(value),)


def _literal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


# An alias identifies a lexical concept in a domain, not a resolved real-world
# object. In particular fish/shellfish and milk/lactose are not interchangeable.
_CONCEPTS = {
    "food.peanut": ("花生", "peanut", "peanuts"),
    "food.shrimp": ("虾", "蝦", "shrimp", "shrimps", "prawn", "prawns"),
    "food.cashew": ("腰果", "cashew", "cashews"),
    "food.almond": ("杏仁", "almond", "almonds"),
    "food.egg": ("鸡蛋", "雞蛋", "egg", "eggs"),
    "food.milk": ("牛奶", "milk"),
    "food.lactose": ("乳糖", "lactose"),
    "food.wheat": ("小麦", "小麥", "wheat"),
    "food.gluten": ("麸质", "麩質", "gluten"),
    "food.fish": ("鱼", "魚", "fish"),
    "food.shellfish": ("贝类", "貝類", "shellfish"),
    "drink.tea": ("茶", "tea"),
    "drink.black_tea": ("红茶", "紅茶", "black tea"),
    "drink.green_tea": ("绿茶", "綠茶", "green tea"),
    "drink.coffee": ("咖啡", "coffee"),
    "activity.running": ("跑步", "running"),
    "vehicle.tesla": ("特斯拉", "tesla"),
    "vehicle.bmw": ("宝马", "寶馬", "bmw"),
}
ALIASES = MappingProxyType({k: tuple(v) for k, v in _CONCEPTS.items()})


def entity_key(value: str, kind: str) -> str:
    """Exact concept alias or a typed literal. Never drop meaningful modifiers."""
    raw = _literal(value)
    for concept, aliases in ALIASES.items():
        eligible = ((kind == "allergen" and concept.startswith("food."))
                    or (kind == "preference" and not concept.startswith("vehicle."))
                    or (kind == "vehicle" and concept.startswith("vehicle.")))
        if eligible and any(raw == _literal(alias) for alias in aliases):
            return concept
    return "literal:" + kind + ":" + raw


@dataclass(frozen=True)
class SemanticKey:
    subject: str = field(repr=False)
    predicate: str = field(repr=False)
    object: str = field(repr=False)


def fact_key(subject: str, relation: str, obj: str) -> SemanticKey:
    p = canonical_predicate(relation)
    kind = "preference" if p in ("likes", "dislikes") else "vehicle" if p == "uses_vehicle" else "value"
    return SemanticKey(subject, p or "custom:" + str(relation), entity_key(obj, kind))
