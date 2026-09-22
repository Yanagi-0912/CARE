"""將結構化人物指涉對到使用者家庭名單中的一位成員。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence

from app.models.family_tree import FamilyMember

# 問自己時 prompt 要模型留空；這些是模型仍把第一人稱原樣填進來時的保底。
_SELF_WORDS = frozenset({"我", "自己", "我自己", "本人", "me", "myself", "i"})

# relationship 由模型填，族譜裡能拿來對人的只有這六種（`other` 對不出是誰）。
# 英文親屬詞是模型偶爾不照 prompt、直接寫出來的說法。
_RELATIONSHIP_ALIASES: dict[str, str] = {
    "parent": "parent", "mother": "parent", "father": "parent", "mom": "parent", "dad": "parent",
    "child": "child", "son": "child", "daughter": "child",
    "spouse": "spouse", "husband": "spouse", "wife": "spouse",
    "sibling": "sibling", "brother": "sibling", "sister": "sibling",
    "grandparent": "grandparent", "grandmother": "grandparent", "grandfather": "grandparent",
    "grandma": "grandparent", "grandpa": "grandparent",
    "grandchild": "grandchild", "grandson": "grandchild", "granddaughter": "grandchild",
}


@dataclass(frozen=True)
class PersonResolution:
    kind: Literal["self", "member", "ambiguous", "not_found", "conflict"]
    member: Optional[FamilyMember] = None
    candidates: tuple[FamilyMember, ...] = ()
    display_label: str = ""
    relationship: Optional[str] = None
    matched_by: Literal[
        "default", "self_word", "name", "relationship", "name_and_relationship", "none"
    ] = "none"


def _normalize(text: Optional[str]) -> str:
    return "".join((text or "").split()).casefold()


def _display_text(text: Optional[str]) -> str:
    return " ".join((text or "").split())


def resolve_person(
    members: Sequence[FamilyMember], *, person: str, relationship: str
) -> PersonResolution:
    """把使用者的說法對到名單裡的一位家人：先比名字、再比關係，對到多位就反問。

    名字比對是雙向包含（「美玲」對得到「王美玲」）。關係只到「父／母」這一層，
    爸媽都在名單裡時分不出來——這時回 ambiguous 讓使用者選，不猜：猜錯就是把
    另一位家人的資料講給他聽。
    """
    wanted = _normalize(person)
    requested_label = _display_text(person)
    raw_relationship = _normalize(relationship)
    relation = _RELATIONSHIP_ALIASES.get(raw_relationship)
    if wanted in _SELF_WORDS:
        return PersonResolution(kind="self", matched_by="self_word")
    if not wanted and not raw_relationship:
        return PersonResolution(kind="self", matched_by="default")

    if wanted:
        by_name = [member for member in members if _name_matches(member, wanted)]
        if by_name:
            return _pick_name_matches(by_name, relation, requested_label)
    if relation is not None:
        by_relation = [
            member for member in members if member.relationship_type == relation
        ]
        if by_relation:
            return _pick_relationship_matches(
                by_relation, relation, requested_label
            )
    return PersonResolution(
        kind="not_found",
        display_label=requested_label or _display_text(relationship),
        relationship=relation,
    )


def _name_matches(member: FamilyMember, wanted: str) -> bool:
    name = _normalize(member.display_name)
    return bool(name) and (wanted in name or name in wanted)


def _member_label(member: FamilyMember, fallback: str) -> str:
    return _display_text(member.display_name) or fallback or member.relationship_type or ""


def _pick_name_matches(
    candidates: list[FamilyMember], relation: Optional[str], requested_label: str
) -> PersonResolution:
    matched_by: Literal["name", "name_and_relationship"] = "name"
    if relation is not None:
        narrowed = [member for member in candidates if member.relationship_type == relation]
        if not narrowed:
            return PersonResolution(
                kind="conflict",
                candidates=tuple(candidates),
                display_label=requested_label,
                relationship=relation,
                matched_by="name",
            )
        candidates = narrowed
        matched_by = "name_and_relationship"
    if len(candidates) == 1:
        member = candidates[0]
        return PersonResolution(
            kind="member",
            member=member,
            display_label=_member_label(member, requested_label),
            relationship=member.relationship_type,
            matched_by=matched_by,
        )
    return PersonResolution(
        kind="ambiguous",
        candidates=tuple(candidates),
        display_label=requested_label,
        relationship=relation,
        matched_by=matched_by,
    )


def _pick_relationship_matches(
    candidates: list[FamilyMember], relation: str, requested_label: str
) -> PersonResolution:
    if len(candidates) == 1:
        member = candidates[0]
        return PersonResolution(
            kind="member",
            member=member,
            display_label=_member_label(member, requested_label or relation),
            relationship=relation,
            matched_by="relationship",
        )
    return PersonResolution(
        kind="ambiguous",
        candidates=tuple(candidates),
        display_label=requested_label or relation,
        relationship=relation,
        matched_by="relationship",
    )


__all__ = ["PersonResolution", "resolve_person"]
