"""以登入者自己的視角查詢家庭名單與稱謂。"""

from __future__ import annotations

import logging
from typing import Any

from app.i18n.messages import t
from app.models.family_tree import FAMILY_RELATIONSHIP_TYPES, FamilyMember
from app.services.family.person_resolution import resolve_person

logger = logging.getLogger(__name__)


class FamilyDirectoryService:
    """只使用並呈現家庭名單中的姓名與稱謂，不碰健康或授權欄位。"""

    def __init__(
        self,
        family_tree_repository: Any,
        user_profile_repository: Any | None = None,
    ) -> None:
        self._trees = family_tree_repository
        self._profiles = user_profile_repository

    async def describe(
        self,
        operator_id: str,
        *,
        person: str = "",
        relationship: str = "",
        language: str | None = None,
    ) -> str:
        try:
            tree = await self._trees.get_by_user_id(operator_id)
        except Exception as exc:
            logger.warning("家庭名單查詢失敗：%s", exc)
            return t("family.directory.error", language)

        members = tuple(tree.family_members) if tree is not None else ()
        relation = relationship.strip().casefold()
        if relation and relation not in FAMILY_RELATIONSHIP_TYPES:
            return t("family.directory.unsupported_relationship", language)

        if person.strip():
            operator_name = await self._get_operator_name(operator_id)
            return self._describe_person(
                members,
                person=person,
                relationship=relation,
                operator_name=operator_name,
                language=language,
            )
        if not members:
            return t("family.directory.empty", language)
        if relation:
            return self._describe_relationship(members, relation, language)
        return self._describe_all(members, language)

    async def _get_operator_name(self, operator_id: str) -> str:
        if self._profiles is None:
            return ""
        try:
            return (await self._profiles.get_display_name(operator_id) or "").strip()
        except Exception as exc:
            # 本人姓名只是改善查詢語意；讀取失敗時仍可照常查家庭名單。
            logger.warning("登入者姓名查詢失敗：%s", exc)
            return ""

    def _describe_person(
        self,
        members: tuple[FamilyMember, ...],
        *,
        person: str,
        relationship: str,
        operator_name: str,
        language: str | None,
    ) -> str:
        resolution = resolve_person(
            members,
            person=person,
            relationship=relationship,
        )
        if resolution.kind == "self":
            return self._describe_self(operator_name, language)

        if not relationship and self._same_name(person, operator_name):
            family_matches = [
                member
                for member in members
                if self._same_name(person, member.display_name or "")
            ]
            if family_matches:
                return t("family.directory.self_ambiguous", language).format(
                    name=operator_name
                )
            return self._describe_self(operator_name, language)

        if resolution.kind == "member" and resolution.member is not None:
            name = self._name(resolution.member, language)
            relation = resolution.member.relationship_type
            if relation in FAMILY_RELATIONSHIP_TYPES:
                return t("family.directory.person", language).format(
                    name=name,
                    relationship=self._relationship_label(relation, language),
                )
            return t("family.directory.person_unset", language).format(name=name)
        if resolution.kind == "ambiguous":
            return t("family.directory.ambiguous", language).format(
                names=self._join(
                    [self._name(member, language) for member in resolution.candidates],
                    language,
                )
            )
        if resolution.kind == "conflict":
            return t("family.directory.conflict", language).format(
                query=person.strip()
            )
        return t("family.directory.not_found", language).format(query=person.strip())

    @staticmethod
    def _describe_self(name: str, language: str | None) -> str:
        if name:
            return t("family.directory.self", language).format(name=name)
        return t("family.directory.self_unnamed", language)

    @staticmethod
    def _same_name(left: str, right: str) -> bool:
        def normalize(value: str) -> str:
            return "".join(value.split()).casefold()

        normalized_left = normalize(left)
        return bool(normalized_left) and normalized_left == normalize(right)

    def _describe_relationship(
        self,
        members: tuple[FamilyMember, ...],
        relationship: str,
        language: str | None,
    ) -> str:
        matches = [
            self._name(member, language)
            for member in members
            if member.relationship_type == relationship
        ]
        label = self._relationship_label(relationship, language)
        if not matches:
            return t("family.directory.no_relationship", language).format(
                relationship=label
            )
        return t("family.directory.relationship", language).format(
            relationship=label,
            names=self._join(matches, language),
        )

    def _describe_all(
        self,
        members: tuple[FamilyMember, ...],
        language: str | None,
    ) -> str:
        unset = t("family.directory.relationship.unset", language)
        entries = [
            t("family.directory.list_item", language).format(
                name=self._name(member, language),
                relationship=(
                    self._relationship_label(member.relationship_type, language)
                    if member.relationship_type in FAMILY_RELATIONSHIP_TYPES
                    else unset
                ),
            )
            for member in members
        ]
        return f'{t("family.directory.list_header", language)}\n' + "\n".join(entries)

    @staticmethod
    def _name(member: FamilyMember, language: str | None) -> str:
        return (member.display_name or "").strip() or t(
            "family.directory.unnamed", language
        )

    @staticmethod
    def _relationship_label(relationship: str, language: str | None) -> str:
        return t(f"family.directory.relationship.{relationship}", language)

    @staticmethod
    def _join(names: list[str], language: str | None) -> str:
        return t("family.directory.list_sep", language).join(names)


__all__ = ["FamilyDirectoryService"]
