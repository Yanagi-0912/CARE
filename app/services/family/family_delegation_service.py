"""委任授權的建立與撤銷。

**啟用的閘門在這裡，而且目前是關著的。** 委任是本系統唯一一條「不經資料
擁有者同意就取得其資料權限」的路徑，它的核可流程——身分驗證、醫療證明、
法定監護證明的形式與查驗方式——是法律問題，由後續的產品／法務 change 定義。
在那之前，建立委任的路徑對終端使用者一律回 404，表現得像這個功能不存在
（沿用 `require_prescription_scan_enabled` 的既有形狀）。

先做一個猜出來的驗證流程再回頭補，比留白危險得多：一個猜出來的閘門會被當成
真的閘門用，然後在某次資安檢視時才發現它只是個表單。

**撤銷不受閘門限制。** 擁有者恆可撤銷其資料上的任何委任——閘門管的是「能不能
給出去」，不是「能不能收回來」。把撤銷一起關掉，會讓已經存在的委任在流程確定
之前無法解除。
"""

import logging
from typing import Any, List, Optional

from fastapi import HTTPException

from app.models.family_tree import DELEGATION_DEFAULT_VALID_DAYS, FamilyDelegation

logger = logging.getLogger(__name__)


class FamilyDelegationService:
    """受委任 GUARDIAN 的建立、撤銷與查詢。"""

    def __init__(
        self,
        delegation_repository: Any,
        family_tree_repository: Any,
        audit_repository: Any,
        activation_enabled: bool = False,
    ) -> None:
        self._delegations = delegation_repository
        self._trees = family_tree_repository
        self._audit = audit_repository
        self._activation_enabled = activation_enabled

    def _require_activation(self) -> None:
        if not self._activation_enabled:
            raise HTTPException(status_code=404, detail="Not Found")

    async def grant(
        self,
        owner_id: str,
        delegate_user_id: str,
        granted_by: str,
        approval_ref: Optional[str] = None,
        valid_days: int = DELEGATION_DEFAULT_VALID_DAYS,
    ) -> FamilyDelegation:
        """建立一筆委任。核可流程確定之前，這條路徑不對終端使用者開放。

        受委任者 SHALL 已是該擁有者族譜中的成員——委任提升的是既有成員的
        權限，不是把陌生人放進照護圈。少了這道檢查，委任就成了繞過家庭邊界
        的入口，而家庭邊界是整套授權最外層的閘門。

        委任 SHALL NOT 跨家庭：這筆紀錄只對 `owner_id` 這一位擁有者的資料
        生效，受委任者對其他擁有者的角色完全不受影響。那是資料模型自然保證
        的——紀錄上帶著 owner_id，判定時逐一比對——但這裡明講，免得日後有人
        以為「他是受委任者」是一種全域身分。
        """
        self._require_activation()

        if owner_id == delegate_user_id:
            raise HTTPException(
                status_code=400,
                detail="無法將委任授予資料擁有者本人：他對自己的資料本來就是 OWNER",
            )

        tree = await self._trees.get_by_user_id(owner_id)
        is_member = tree is not None and any(
            m.user_id == delegate_user_id for m in tree.family_members
        )
        if not is_member:
            raise HTTPException(
                status_code=404,
                detail=f"在此家庭中找不到成員 {delegate_user_id}",
            )

        delegation = await self._delegations.grant(
            owner_id=owner_id,
            delegate_user_id=delegate_user_id,
            granted_by=granted_by,
            approval_ref=approval_ref,
            valid_days=valid_days,
        )
        await self._audit.append(
            owner_id=owner_id,
            member_id=delegate_user_id,
            changed_by=granted_by,
            to_role="GUARDIAN",
            via_delegation=True,
            event="delegation_granted",
        )
        return delegation

    async def revoke(
        self, operator_id: str, owner_id: str, delegate_user_id: str
    ) -> int:
        """撤銷委任。**僅資料擁有者本人可以。**

        不接受受委任者自行撤銷他人的委任：那會讓兩個受委任者互相解除，而擁有
        者可能正處於無法表達意願的狀態，看不到也管不了。撤銷是收回權力的動作，
        只有權力的來源可以做。

        擁有者恢復操作能力後撤銷委任期間建立的委任，走的也是這條路徑——那正是
        最需要留下痕跡的情境，因此一律寫稽核。
        """
        if operator_id != owner_id:
            raise HTTPException(
                status_code=403,
                detail="權限不足：僅資料擁有者本人可以撤銷委任",
            )

        revoked = await self._delegations.revoke(
            owner_id=owner_id,
            delegate_user_id=delegate_user_id,
            revoked_by=operator_id,
        )
        if revoked:
            await self._audit.append(
                owner_id=owner_id,
                member_id=delegate_user_id,
                changed_by=operator_id,
                from_role="GUARDIAN",
                via_delegation=False,
                event="delegation_revoked",
            )
        logger.info(
            "委任撤銷完成：owner=%s, delegate=%s, count=%s",
            owner_id,
            delegate_user_id,
            revoked,
        )
        return revoked

    async def list_active(self, operator_id: str, owner_id: str) -> List[FamilyDelegation]:
        """列出某位擁有者當下有效的委任。僅擁有者本人可查。

        「誰代我行事」是擁有者的資訊，不是家庭公開資訊。
        """
        if operator_id != owner_id:
            raise HTTPException(
                status_code=403,
                detail="權限不足：僅資料擁有者本人可以查看委任紀錄",
            )
        return await self._delegations.list_active(owner_id)
