"""家庭角色的指派與引導式指派狀態。

這裡是提權防護的所在。六道檢查各自封死一條路，順序有意義——**「有沒有資格
碰這份文件」永遠先於「要對這份文件做什麼」**：任何在資格判定之前就讀取目標
族譜、或依讀到的內容決定要不要放行的寫法，都等於用資料本身當授權依據。

授權判定一律委由 `FamilyAuthorizationService`，這個模組不自行判斷「他是不是
家人」「他是什麼角色」——那正是本 change 要消滅的散落邏輯。
"""

import logging
from typing import Any, List, Optional

from fastapi import HTTPException

from app.models.family_authorization import ASSIGNABLE_FAMILY_ROLES
from app.models.family_tree import (
    FamilyRoleAssignmentStatus,
    FamilyRoleEntry,
    FamilyTree,
)

logger = logging.getLogger(__name__)


class FamilyRoleService:
    """角色指派。唯一會呼叫 `FamilyTreeRepository.set_family_role` 的地方。"""

    def __init__(
        self,
        authorization_service: Any,
        family_tree_repository: Any,
        audit_repository: Any,
    ) -> None:
        self._authz = authorization_service
        self._trees = family_tree_repository
        self._audit = audit_repository

    async def _require_management_rights(
        self, operator_id: str, owner_id: str
    ) -> bool:
        """檢查 1：呼叫者有沒有資格管理這位擁有者的角色。回傳是否經由委任。

        **這一步在讀取目標族譜之前完成。** 順序不是風格問題：先讀族譜再判斷，
        就會出現「用目標族譜的內容決定要不要放行」的寫法，而那份內容正是被
        管理的對象。資格必須來自呼叫者與擁有者的關係，不是來自要改的資料。
        """
        if operator_id == owner_id:
            return False

        if not await self._authz.is_active_delegate(operator_id, owner_id):
            # 訊息不透露該擁有者是否存在、也不透露呼叫者在不在其族譜內：
            # 呼叫者本來就不該知道自己「差一點」有資格。
            raise HTTPException(
                status_code=403,
                detail="權限不足：您無權管理此使用者的家庭成員角色",
            )
        return True

    async def assign_role(
        self,
        operator_id: str,
        owner_id: str,
        member_id: str,
        family_role: str,
    ) -> FamilyTree:
        """指派某位成員在某位擁有者族譜中的角色。

        六道檢查：

        1. 呼叫者非擁有者本人時，先過委任判定（讀族譜之前）。
        2. 目標成員必須在該擁有者的族譜裡，否則 404。
        3. 目標成員不得是擁有者自己 —— 400。`OWNER` 是推導值，沒有可修改的
           對象；允許改它等於允許擁有者把自己降級，然後整份資料沒有人管得動。
        4. `OWNER` 不是可指派的值 —— 400。
        5. 受委任者不得授予 `GUARDIAN` —— 403。只有擁有者本人能造出 GUARDIAN，
           否則委任鏈就成立了：受委任者造一個 GUARDIAN，那個人再造下一個。
        6. 結構性：這條路徑寫入的文件恆為 `owner_id` 的族譜，`member_id` 只是
           陣列裡的一個元素。未受委任者過不了檢查 1，也就永遠到不了寫入。
        """
        via_delegation = await self._require_management_rights(operator_id, owner_id)

        # 檢查 3：先於族譜查詢，因為它與族譜內容無關——擁有者自己的角色是
        # 推導值，不論他在不在自己的成員清單裡都不該被指派。
        if member_id == owner_id:
            raise HTTPException(
                status_code=400,
                detail="無法指派資料擁有者本人的角色：OWNER 由資料歸屬推導而來，不是可授予的角色",
            )

        # 檢查 4
        if family_role not in ASSIGNABLE_FAMILY_ROLES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"不可指派的家庭角色：{family_role}。"
                    f"可用值：{sorted(ASSIGNABLE_FAMILY_ROLES)}"
                ),
            )

        # 檢查 5
        if via_delegation and family_role == "GUARDIAN":
            raise HTTPException(
                status_code=403,
                detail="受委任者不得授予 GUARDIAN：僅資料擁有者本人可以",
            )

        # 檢查 2：走到這裡才讀族譜。
        tree = await self._trees.get_by_user_id(owner_id)
        member = None
        if tree is not None:
            member = next(
                (m for m in tree.family_members if m.user_id == member_id), None
            )
        if member is None:
            raise HTTPException(
                status_code=404,
                detail=f"在此家庭中找不到成員 {member_id}",
            )

        previous_role = member.family_role
        updated = await self._trees.set_family_role(owner_id, member_id, family_role)
        if updated is None:
            # 讀到之後、寫入之前成員被移除。不當成成功，也不重試——重試會在
            # 一個已經不存在的成員上建立角色。
            raise HTTPException(
                status_code=404,
                detail=f"在此家庭中找不到成員 {member_id}",
            )

        await self._audit.append(
            owner_id=owner_id,
            member_id=member_id,
            changed_by=operator_id,
            from_role=previous_role,
            to_role=family_role,
            via_delegation=via_delegation,
            event="role_change",
        )
        logger.info(
            "家庭角色已指派：owner=%s, member=%s, role=%s, via_delegation=%s",
            owner_id,
            member_id,
            family_role,
            via_delegation,
        )
        return updated

    async def list_roles(
        self, operator_id: str, owner_id: str
    ) -> List[FamilyRoleEntry]:
        """列出某位擁有者族譜中每位成員的角色。

        與指派同一道資格閘門：能看到「誰有什麼權限」本身就是管理資訊，不該對
        一般成員開放——那等於把整個家庭的授權結構攤開給權限最低的人看。
        """
        await self._require_management_rights(operator_id, owner_id)

        tree = await self._trees.get_by_user_id(owner_id)
        if tree is None:
            return []
        return [
            FamilyRoleEntry(
                user_id=m.user_id,
                display_name=m.display_name,
                family_role=m.family_role,
                effective_family_role=m.effective_family_role,
            )
            for m in tree.family_members
        ]

    async def assignment_status(
        self, operator_id: str, owner_id: str
    ) -> FamilyRoleAssignmentStatus:
        """引導式角色指派的完成狀態，由後端依族譜資料判定。"""
        await self._require_management_rights(operator_id, owner_id)
        return await self._authz.role_assignment_status(owner_id)
