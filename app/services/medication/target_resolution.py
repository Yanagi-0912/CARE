"""「這個問題要問誰的藥」——用藥三條路徑共用的對象解析與授權。

原本只長在 `MedicationStatusService._describe` 裡。現在有三條路徑要做同一件事：

- 查清單（`MedicationStatusService`）
- 拿自己的藥單問問題（`MedicationQuestionService`）
- 在聊天裡回報吃過了（`MedicationReportService`）

各自抄一份的風險不是重複而是分岔：家人授權（`authorize`）只要有一條路徑忘了
呼叫，那條路徑就是一個繞過 RBAC 的洞，而且從外面看不出來——三條路徑回的都是
藥名，看起來一樣正常。所以把「解析＋授權」綁成同一個不可分割的呼叫。

回傳而不是丟例外：這幾段文字會原樣送給使用者（見各服務的直通說明），
丟例外只會讓模型接手、自己編一個答案。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from fastapi import HTTPException

from app.i18n.messages import t
from app.models.family_tree import FamilyMember
from app.services.family.person_resolution import resolve_person


@dataclass
class MedicationTarget:
    """解析結果。`error` 有值時就是要送給使用者的整段文字，其餘欄位不可用。"""

    user_id: str = ""
    # None＝問的是自己。呼叫端據此挑「您的…」還是「{name}的…」文案。
    display_name: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def resolve_medication_target(
    *,
    asker_id: str,
    person: str,
    relationship: str,
    trees,
    authz,
    language: Optional[str] = None,
    classification: str = "GENERAL",
    action: str = "READ",
) -> MedicationTarget:
    """把使用者指稱的對象換成 user_id，並在對象不是本人時走授權。

    `classification`／`action` 留成參數而不是寫死 GENERAL/READ：回報服藥是寫入，
    授權矩陣上不是同一格。呼叫端各自宣告自己要的那一格，這裡不替它決定。
    """
    if resolve_person((), person=person, relationship=relationship).kind == "self":
        return MedicationTarget(user_id=asker_id, display_name=None)

    tree = await trees.get_by_user_id(asker_id)
    members = [m for m in (tree.family_members if tree else []) if m.user_id != asker_id]
    if not members:
        return MedicationTarget(error=t("medstatus.no_family", language))

    resolution = resolve_person(members, person=person, relationship=relationship)
    if resolution.kind == "conflict":
        return MedicationTarget(
            error=t("medstatus.conflict", language).format(
                query=(person or relationship).strip()
            )
        )
    if resolution.kind == "ambiguous":
        return MedicationTarget(
            error=t("medstatus.ambiguous", language).format(
                names=names_of(resolution.candidates, language)
            )
        )
    if resolution.kind == "not_found":
        return MedicationTarget(
            error=t("medstatus.not_found", language).format(
                query=(person or relationship).strip(),
                names=names_of(members, language),
            )
        )

    target_name = display_name_of(resolution.member, language)
    try:
        # 「吃了沒」與藥名、服藥時段同屬 GENERAL：回答的都是「這一餐的藥處理了
        # 沒」，不揭露為什麼吃。這條路徑導入 RBAC 前不存在，照
        # has_legacy_equivalent=False 一律以矩陣判定，不受影子模式放寬。
        await authz.authorize(
            asker_id,
            resolution.member.user_id,
            classification,
            action,
            has_legacy_equivalent=False,
        )
    except HTTPException:
        return MedicationTarget(
            error=t("medstatus.no_permission", language).format(name=target_name)
        )

    return MedicationTarget(user_id=resolution.member.user_id, display_name=target_name)


def display_name_of(member: FamilyMember, language: Optional[str]) -> str:
    return member.display_name or t("medstatus.unnamed", language)


def names_of(members: Sequence[FamilyMember], language: Optional[str]) -> str:
    return t("medstatus.list_sep", language).join(
        display_name_of(m, language) for m in members
    )
