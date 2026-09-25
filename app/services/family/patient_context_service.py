"""安全地解析看診者，並在授權後才套用健康資料。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from app.services.family.patient_context import PatientContext, build_patient_context
from app.services.family.person_resolution import PersonResolution, resolve_person

logger = logging.getLogger(__name__)


class PatientContextService:
    """建立看診者脈絡；人物命中本身不代表可讀取其健康資料。"""

    def __init__(
        self,
        *,
        family_tree_repository: Any,
        authorization_service: Any,
        user_profile_service: Any,
    ) -> None:
        self._trees = family_tree_repository
        self._authorization = authorization_service
        self._profiles = user_profile_service

    async def resolve(
        self,
        operator_id: str,
        *,
        person: str = "",
        relationship: str = "",
        message_age: object = None,
        message_gender: object = None,
        external_patient_id: str | None = None,
    ) -> PatientContext:
        """解析人物並合併可用資料。

        ``external_patient_id`` 刻意不參與人物命中或授權。它可供未來接線層傳入
        既有欄位而不會形成 IDOR：知道某個 user id，不等於能把該人設成看診者。
        """
        del external_patient_id

        resolution = await self.resolve_person(
            operator_id, person=person, relationship=relationship
        )
        profile = await self._authorized_profile(operator_id, resolution)
        return build_patient_context(
            operator_id=operator_id,
            resolution=resolution,
            message_age=message_age,
            message_gender=message_gender,
            profile_age=(profile or {}).get("age"),
            profile_gender=(profile or {}).get("gender"),
        )

    async def resolve_person(
        self,
        operator_id: str,
        *,
        person: str,
        relationship: str,
    ) -> PersonResolution:
        """只把說法對到家庭名單中的一位，不讀任何健康資料、不做授權。

        緊急流程用它決定稱謂與通知對象：命中家人不等於能讀他的 profile。
        名單讀不到時退回「無名單」的解析結果（不會是 member），不猜。
        """
        without_members = resolve_person((), person=person, relationship=relationship)
        if without_members.kind == "self":
            return without_members

        try:
            tree = await self._trees.get_by_user_id(operator_id)
        except Exception as exc:
            logger.warning("無法讀取家庭名單，人物解析採安全降級：%s", exc)
            return without_members

        members = tree.family_members if tree is not None else ()
        return resolve_person(members, person=person, relationship=relationship)

    async def _authorized_profile(
        self,
        operator_id: str,
        resolution: PersonResolution,
    ) -> dict[str, Any] | None:
        if resolution.kind == "self":
            return await self._load_profile(operator_id)
        if resolution.kind != "member" or resolution.member is None:
            return None

        patient_id = resolution.member.user_id
        try:
            await self._authorization.authorize(
                operator_id,
                patient_id,
                "SENSITIVE",
                "READ",
                has_legacy_equivalent=False,
            )
        except HTTPException:
            return None
        except Exception as exc:
            logger.warning("看診者健康資料授權失敗，採安全降級：%s", exc)
            return None
        return await self._load_profile(patient_id)

    async def _load_profile(self, patient_id: str) -> dict[str, Any] | None:
        try:
            return await self._profiles.get_user_profile(patient_id)
        except Exception as exc:
            logger.warning("無法讀取看診者健康資料，採安全降級：%s", exc)
            return None


__all__ = ["PatientContextService"]
