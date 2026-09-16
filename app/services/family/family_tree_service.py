import logging
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from fastapi import HTTPException

from app.core.config import settings
from app.models.family_tree import (
    FamilyMember,
    FamilyTree,
    REVERSE_RELATIONSHIP,
    PendingInvitation,
)
from app.models.family_authorization import ASSIGNABLE_FAMILY_ROLES
from app.repositories.family_delegation_repository import FamilyDelegationRepository
from app.repositories.family_tree_repository import FamilyTreeRepository
from app.services.family.rbac_migration import enforce_if_assignment_complete
logger = logging.getLogger(__name__)


class FamilyTreeService:
    """
    家庭服務功能。
    """

    def __init__(
        self,
        repository: Any = FamilyTreeRepository,
        audit_repository: Any = None,
        delegation_repository: Any = FamilyDelegationRepository,
    ) -> None:
        """repository 可注入，讓測試以假物件替代而不必 monkey patch
        （openspec/config.yaml 的測試規則）。預設值即原本直接呼叫的那個類別，
        既有呼叫端與既有測試都不受影響。

        `audit_repository` 給移除成員寫稽核用；未注入時不寫（只有測試會這樣建）。

        `delegation_repository` 給移除成員時撤銷委任用。預設就是真的 repository，
        與 `repository` 同一個形狀——不給「不撤銷」這個選項。"""
        self._repo = repository
        self._audit = audit_repository
        self._delegations = delegation_repository

    async def get_family_tree(self, user_id: str) -> FamilyTree:
        """
        取得族譜，若尚不存在則建立空族譜並回傳。
        使用 MongoDB Aggregation $lookup 進行資料庫關聯查詢以確保效能與實時更新。
        """
        await self._repo.upsert_tree(user_id)
        tree = await self._repo.get_by_user_id(user_id)
        assert tree is not None
        return tree

    async def create_invitation(
        self,
        inviter_id: str,
        owner_id: Optional[str] = None,
        family_role: Optional[str] = None,
        authorization_service: Optional[Any] = None,
    ) -> PendingInvitation:
        """
        建立邀請碼與過期時間，並存入資料庫。

        邀請可指定受邀者加入**哪一位擁有者**的照護圈、以及加入後的角色。
        四道限制同時成立，各自堵住一條路：

        1. 角色於建立時保存於邀請記錄，`accept` 一律忽略客戶端帶來的角色——
           否則邀請連結被轉發後，取得者可自選角色。
        2. `owner_id` 指向他人時需要該擁有者的有效委任，否則任何人都能把
           陌生人塞進長輩的照護圈。
        3. `GUARDIAN` 僅擁有者本人可指定；受委任者建立的邀請限
           `CAREGIVER`／`MEMBER`，避免委任鏈。
        4. 邀請只作用於尚非成員者（見 `accept_invitation`）——否則能建立邀請
           的人只要把自己「重新加入」一次就完成提權，前三道全部繞過。
        """
        target_owner_id = owner_id or inviter_id

        if family_role is not None and family_role not in ASSIGNABLE_FAMILY_ROLES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"不可指派的家庭角色：{family_role}。"
                    f"可用值：{sorted(ASSIGNABLE_FAMILY_ROLES)}"
                ),
            )

        if target_owner_id != inviter_id:
            if authorization_service is None:
                # 沒有授權服務就無從判定資格。fail-closed：拒絕，不放行。
                raise HTTPException(
                    status_code=403,
                    detail="權限不足：無法為其他使用者建立邀請",
                )
            if not await authorization_service.is_active_delegate(
                inviter_id, target_owner_id
            ):
                raise HTTPException(
                    status_code=403,
                    detail="權限不足：您無權為此使用者建立家庭邀請",
                )
            if family_role == "GUARDIAN":
                raise HTTPException(
                    status_code=403,
                    detail="受委任者不得透過邀請授予 GUARDIAN：僅資料擁有者本人可以",
                )

        token = secrets.token_urlsafe(8)
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=7)

        invitation = await self._repo.save_invitation(
            token=token,
            inviter_id=inviter_id,
            expires_at=expires_at,
            owner_id=target_owner_id,
            family_role=family_role,
        )

        logger.info(
            "邀請已建立：inviter=%s, owner=%s, role=%s, token=%s",
            inviter_id,
            target_owner_id,
            family_role,
            token,
        )
        return invitation

    @staticmethod
    def _is_usable(invitation: PendingInvitation) -> bool:
        """這筆邀請此刻還能不能被接受。

        「可用」的定義只寫在這裡一處。`verify_invitation`、`accept_invitation`
        與 QR 圖片端點都問這支——三邊各自判斷的話，只要有一邊算法不同，就會
        出現「QR 看起來還有效、按下去卻 410」這種使用者無從理解的狀態。
        """
        # 只有 pending 能用：accepted、revoked，以及任何日後新增的狀態一律不能。
        # 寫成「不是 pending 就不行」而不是列舉不能的狀態，新增狀態時才不會漏。
        if invitation.status != "pending":
            return False

        expires_at = invitation.expires_at
        # Mongo 取回的 datetime 多半是 naive UTC；已經帶時區的就不要動它，
        # 無條件 replace() 會把別的時區當成 UTC 而算錯瞬間。
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        return datetime.now(tz=timezone.utc) <= expires_at

    async def is_invitation_usable(self, code: str) -> bool:
        """邀請碼是否有效。不存在與已失效一律回 False。

        給 QR 圖片端點用。那支端點刻意不區分這兩種情況——區分了就等於送出
        一個可以枚舉有效邀請碼的預言機。需要區分的呼叫端請用
        `verify_invitation`，它會用 404／410 分開回報。
        """
        invitation = await self._repo.get_invitation(code)
        return invitation is not None and self._is_usable(invitation)

    async def verify_invitation(self, code: str) -> PendingInvitation:
        """
        驗證邀請碼並取得邀請者名稱。
        """
        invitation = await self._repo.get_invitation(code)

        if invitation is None:
            raise HTTPException(status_code=404, detail="邀請連結無效")

        if not self._is_usable(invitation):
            raise HTTPException(status_code=410, detail="邀請連結已失效")

        return invitation

    async def accept_invitation(
        self, invitee_id: str, code: str
    ) -> tuple[str, str | None]:
        """
        接受邀請並加入家族，處理 already_member 情況。
        回傳 tuple (status, message)。
        """
        invitation = await self._repo.get_invitation(code)

        if invitation is None:
            raise HTTPException(status_code=404, detail="邀請連結無效")

        if not self._is_usable(invitation):
            raise HTTPException(status_code=410, detail="邀請連結已失效")

        owner_id = invitation.target_owner_id
        if owner_id == invitee_id:
            raise HTTPException(status_code=400, detail="無法邀請自己加入族譜")
        if invitation.inviter_id == invitee_id:
            # 受委任者替長輩建的邀請，自己按下接受：他本來就在長輩的族譜裡
            # （委任的前提），上面 already_member 會擋；但長輩那邊若已把他移除
            # 而委任還沒撤銷完，這條路會讓他用自己發的邀請、以自己選的角色
            # 重新加入。發邀請的人與接受的人 SHALL NOT 是同一個人。
            raise HTTPException(status_code=400, detail="無法接受自己建立的邀請")

        owner_tree = await self._repo.get_by_user_id(owner_id)
        if owner_tree and any(
            m.user_id == invitee_id for m in owner_tree.family_members
        ):
            # 既有成員的角色 SHALL NOT 因接受邀請而改變。少了這一條，能建立
            # 邀請的人只要對自己發一張 GUARDIAN 邀請再接受，就完成提權——
            # 前面三道限制全部被繞過。
            return "already_member", "你已是此家庭成員"

        # 先**原子地**把邀請搶下來，再改族譜。兩個人同時接受同一張轉發的邀請，
        # 只有一個會搶到；另一個看到的就是「已失效」。以前是先加人再無條件
        # 標記，兩個人都會進到族譜裡。
        claimed = await self._repo.accept_invitation(code, accepted_by=invitee_id)
        if claimed is None:
            raise HTTPException(status_code=410, detail="邀請連結已失效")

        await self._link_members(owner_id, invitee_id, invitation.family_role)

        return "joined", None

    async def _link_members(
        self, owner_id: str, invitee_id: str, family_role: Optional[str]
    ) -> None:
        """把受邀者與擁有者寫進彼此的族譜。邀請已經在呼叫端搶下來了。"""
        # 確保雙方族譜存在（upsert）
        await self._repo.upsert_tree(owner_id)
        await self._repo.upsert_tree(invitee_id)

        # 1. 擁有者的族譜加入受邀者，帶上邀請記錄裡保存的角色。
        #    角色表達的是「受邀者對擁有者的資料是什麼角色」，是擁有者的授權決定。
        await self._repo.add_member(
            owner_id,
            FamilyMember(user_id=invitee_id, family_role=family_role),
        )

        # 2. 受邀者的族譜加入擁有者，明確給 MEMBER——矩陣裡權限最低的角色，
        #    只能讀 GENERAL（顯示名稱、頭像、用藥設定），SENSITIVE 與 PRIVATE
        #    都不能。
        #
        #    以前這一邊**不帶角色**。「未設定」在授權上雖然等同 MEMBER，但影子
        #    模式只對「有明確角色」的成員套用矩陣（見 FamilyAuthorizationService
        #    ._is_strict）——未設定的成員走的是導入前的寬鬆行為：在族譜裡就
        #    什麼都能讀。結果是受邀者只要接受邀請，邀請者就能看到受邀者的健康
        #    資料與對話紀錄，而受邀者從未表示要授予任何權限。
        #
        #    明確寫 MEMBER 之後，這一邊立刻受矩陣約束。受邀者日後要給更多，
        #    自己去指派。
        await self._repo.add_member(
            invitee_id, FamilyMember(user_id=owner_id, family_role="MEMBER")
        )

        logger.info(
            "成員加入成功：owner=%s, invitee=%s, role=%s",
            owner_id,
            invitee_id,
            family_role,
        )

    async def revoke_invitation(
        self,
        operator_id: str,
        code: str,
        authorization_service: Optional[Any] = None,
    ) -> bool:
        """撤銷一張還沒被接受的邀請。僅該邀請的擁有者本人或其有效受委任者可以。

        回傳這次有沒有真的改到狀態：已撤銷者再撤銷一次回 False 但不報錯
        （按兩次按鈕不該看到錯誤）；已接受者回 409——人已經在族譜裡，要拿掉
        他走的是移除成員，不是撤銷邀請。

        不存在的邀請碼與沒有權限的都回 404：邀請碼是憑證，帶得出來不代表有權
        管理它，而回 403 等於告訴對方「這組碼存在」。
        """
        invitation = await self._repo.get_invitation(code)
        if invitation is None:
            raise HTTPException(status_code=404, detail="邀請連結無效")

        owner_id = invitation.target_owner_id
        permitted = operator_id == owner_id
        if not permitted and authorization_service is not None:
            permitted = await authorization_service.is_active_delegate(
                operator_id, owner_id
            )
        if not permitted:
            raise HTTPException(status_code=404, detail="邀請連結無效")

        if invitation.status == "accepted":
            raise HTTPException(
                status_code=409,
                detail="邀請已被接受，無法撤銷；請改用移除家人",
            )

        revoked = await self._repo.revoke_invitation(code, revoked_by=operator_id)
        logger.info(
            "邀請撤銷：operator=%s, owner=%s, token=%s, changed=%s",
            operator_id,
            owner_id,
            code,
            revoked,
        )
        return revoked

    async def set_relationship(
        self, user_id: str, member_id: str, relationship_type: str
    ) -> FamilyTree:
        """
        更新 user_id 族譜中 member_id 的 relationship_type，
        同時嘗試更新 member_id 族譜中 user_id 的反向關係。
        若 member_id 未將 user_id 加入族譜，則反向更新略過（log）。
        """
        # 驗證 relationship_type 是否合法
        if relationship_type not in REVERSE_RELATIONSHIP:
            raise HTTPException(
                status_code=400,
                detail=f"不支援的關係類型：{relationship_type}。"
                f"可用值：{list(REVERSE_RELATIONSHIP.keys())}",
            )

        # 更新自身族譜
        updated_tree = await self._repo.set_relationship(
            user_id, member_id, relationship_type
        )
        if updated_tree is None:
            raise HTTPException(
                status_code=404,
                detail=f"在 {user_id} 的族譜中找不到成員 {member_id}",
            )

        return updated_tree

    async def set_care_recipient(
        self, user_id: str, member_id: str, is_care_recipient: bool = True
    ) -> FamilyTree:
        """
        設定 user_id 族譜中成員 member_id 的照顧對象標籤 (is_care_recipient)。
        """
        updated_tree = await self._repo.set_care_recipient(
            user_id, member_id, is_care_recipient
        )
        if updated_tree is None:
            raise HTTPException(
                status_code=404,
                detail=f"在 {user_id} 的族譜中找不到成員 {member_id}",
            )
        return updated_tree

    async def remove_member(self, operator_id: str, member_id: str) -> None:
        """切斷操作者與某位家人之間的連結，**兩個方向一起**。

        族譜是雙向登記的（見 `add_to_family`）：我的族譜裡有他（帶他對我的角色），
        他的族譜裡也有我。只拿掉一邊，另一個方向的存取與推播都還在——使用者按的
        是「移除這位家人」，不會預期自己仍看得到對方、或仍收到對方的逾時通報。

        兩個方向都不需要額外授權，各有各的理由：

        - 從**自己**的族譜拿掉對方：自己的族譜只有自己能寫，這是擁有者的權利。
        - 把**自己**從對方的族譜拿掉：只會收回自己對對方資料的存取，是放棄權限
          而不是取得權限。所以受邀的家人也能用同一支端點「退出」長輩的家庭。

        動不到第三者：寫入的兩份文件恆為「操作者的族譜」與「對方的族譜」，
        而且各自只拿掉彼此。
        """
        if member_id == operator_id:
            raise HTTPException(status_code=400, detail="無法移除自己")

        removed_from_mine = await self._repo.remove_member(operator_id, member_id)
        removed_from_theirs = await self._repo.remove_member(member_id, operator_id)
        if removed_from_mine is None and removed_from_theirs is None:
            raise HTTPException(
                status_code=404,
                detail=f"在您的家庭中找不到成員 {member_id}",
            )

        await self._audit_removal(operator_id, removed_from_mine, operator_id)
        await self._audit_removal(member_id, removed_from_theirs, operator_id)

        # 兩個方向的委任一起撤銷。委任提升的是「既有成員」的權限，人都不在
        # 族譜裡了，委任沒有存在的理由；留著的話，重新受邀加入的那一刻它會
        # 直接復活成 GUARDIAN。`is_active_delegate` 另外也要求受委任者仍是
        # 成員，所以這裡失敗只會讓紀錄不乾淨，不會讓權限復活——因此失敗記 log
        # 不擋已經完成的移除。
        for owner_id, delegate_id in ((operator_id, member_id), (member_id, operator_id)):
            await self._revoke_delegation_on_removal(owner_id, delegate_id, operator_id)

        # 移除的可能正是最後一位未設定角色的人：那一刻起剩下的角色設定才生效。
        # 失敗不影響這次移除——成員已經拿掉了；下一次指派或 backfill 腳本會補切。
        for owner_id in (operator_id, member_id):
            try:
                await enforce_if_assignment_complete(self._repo, owner_id)
            except Exception:
                logger.exception("切換家庭權限為強制失敗：owner=%s", owner_id)

        logger.info("成員已移除：operator=%s, member=%s", operator_id, member_id)

    async def _revoke_delegation_on_removal(
        self, owner_id: str, delegate_id: str, changed_by: str
    ) -> None:
        try:
            revoked = await self._delegations.revoke(
                owner_id=owner_id, delegate_user_id=delegate_id, revoked_by=changed_by
            )
        except Exception:
            logger.exception(
                "移除成員時撤銷委任失敗：owner=%s, delegate=%s", owner_id, delegate_id
            )
            return
        if not revoked or self._audit is None:
            return
        try:
            await self._audit.append(
                owner_id=owner_id,
                member_id=delegate_id,
                changed_by=changed_by,
                from_role="GUARDIAN",
                via_delegation=False,
                event="delegation_revoked",
            )
        except Exception:
            logger.exception(
                "撤銷委任的稽核寫入失敗：owner=%s, delegate=%s", owner_id, delegate_id
            )

    async def _audit_removal(
        self, owner_id: str, removed: Optional[FamilyMember], changed_by: str
    ) -> None:
        """移除的稽核。寫不進去只記 log，不讓已經完成的移除變成一個 500——
        使用者會以為沒移除成功而重按，第二次拿到的是 404。"""
        if removed is None or self._audit is None:
            return
        try:
            await self._audit.append(
                owner_id=owner_id,
                member_id=removed.user_id,
                changed_by=changed_by,
                from_role=removed.family_role,
                to_role=None,
                event="member_removed",
            )
        except Exception:
            logger.exception(
                "移除成員的稽核寫入失敗：owner=%s, member=%s", owner_id, removed.user_id
            )

