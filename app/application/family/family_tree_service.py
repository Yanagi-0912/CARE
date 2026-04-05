import logging
from datetime import datetime, timezone

from fastapi import HTTPException

from app.core.config import settings
from app.models.family_tree import (
    FamilyMember,
    FamilyTree,
    REVERSE_RELATIONSHIP,
    SendInvitationResponse,
    AddToFamilyResponse,
)
from app.repositories.family_tree_repository import FamilyTreeRepository

logger = logging.getLogger(__name__)


class FamilyTreeService:
    """家庭族譜業務邏輯層，協調 Repository 操作並處理跨成員的雙向更新。"""

    # ── 發送邀請 ──────────────────────────────────────────────────────────────

    @staticmethod
    async def send_invitation(inviter_id: str) -> SendInvitationResponse:
        """
        建立一筆 PendingInvitation，組合 LIFF 邀請連結並回傳。
        前端取得 invite_url 後，負責透過 liff.shareTargetPicker() 傳送 Flex Message。
        """
        invitation = await FamilyTreeRepository.create_invitation(inviter_id)

        if not settings.LIFF_URL:
            logger.error("LIFF_URL 未設定，無法組合邀請連結")
            raise HTTPException(status_code=500, detail="LIFF_URL is not configured")

        invite_url = f"{settings.LIFF_URL}?inviteId={invitation.id}"

        logger.info(f"邀請已建立：inviter={inviter_id}, invite_id={invitation.id}")
        return SendInvitationResponse(invite_id=invitation.id, invite_url=invite_url)

    # ── 接受邀請，加入族譜 ───────────────────────────────────────────────────

    @staticmethod
    async def add_to_family(invitee_id: str, invite_id: str) -> AddToFamilyResponse:
        """
        驗證邀請後，將 invitee 與 inviter 雙向加入彼此的族譜。
        雙向寫入採 best-effort：若其中一邊失敗則 log 後繼續，不回滾。
        """
        # 1. 查詢並驗證邀請
        invitation = await FamilyTreeRepository.get_invitation(invite_id)

        if invitation is None:
            raise HTTPException(status_code=404, detail="邀請不存在")

        if invitation.status == "accepted":
            raise HTTPException(status_code=409, detail="此邀請已被使用")

        now = datetime.now(tz=timezone.utc)
        # MongoDB 的 datetime 可能是 naive，統一轉為 aware 比較
        expires_at = invitation.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if now > expires_at:
            raise HTTPException(status_code=410, detail="邀請連結已過期")

        inviter_id = invitation.inviter_id

        if inviter_id == invitee_id:
            raise HTTPException(status_code=400, detail="無法邀請自己加入族譜")

        # 2. 確保雙方族譜存在（upsert）
        await FamilyTreeRepository.upsert_tree(inviter_id)
        await FamilyTreeRepository.upsert_tree(invitee_id)

        # 3. inviter 的族譜加入 invitee
        try:
            await FamilyTreeRepository.add_member(
                inviter_id, FamilyMember(user_id=invitee_id)
            )
        except Exception as e:
            logger.error(f"add_to_family：寫入 inviter 族譜失敗 ({inviter_id}): {e}")

        # 4. invitee 的族譜加入 inviter（best-effort，失敗不中斷）
        try:
            await FamilyTreeRepository.add_member(
                invitee_id, FamilyMember(user_id=inviter_id)
            )
        except Exception as e:
            logger.error(f"add_to_family：寫入 invitee 族譜失敗 ({invitee_id}): {e}")

        # 5. 標記邀請為已使用
        await FamilyTreeRepository.accept_invitation(invite_id)

        # 6. 回傳 invitee 最新族譜
        family_tree = await FamilyTreeRepository.get_by_user_id(invitee_id)
        logger.info(f"成員加入成功：inviter={inviter_id}, invitee={invitee_id}")
        return AddToFamilyResponse(success=True, family_tree=family_tree)

    # ── 取得族譜 ──────────────────────────────────────────────────────────────

    @staticmethod
    async def get_family_tree(user_id: str) -> FamilyTree:
        """取得族譜；若尚不存在則建立空族譜並回傳。"""
        return await FamilyTreeRepository.upsert_tree(user_id)

    # ── 設定關係類型（雙向）────────────────────────────────────────────────────

    @staticmethod
    async def set_relationship(
        user_id: str, member_id: str, relationship_type: str
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

        # 1. 更新自身族譜
        updated_tree = await FamilyTreeRepository.set_relationship(
            user_id, member_id, relationship_type
        )
        if updated_tree is None:
            raise HTTPException(
                status_code=404,
                detail=f"在 {user_id} 的族譜中找不到成員 {member_id}",
            )

        # 2. 計算反向關係並嘗試更新對方族譜（best-effort）
        reverse_rel = REVERSE_RELATIONSHIP[relationship_type]
        try:
            result = await FamilyTreeRepository.set_relationship(
                member_id, user_id, reverse_rel
            )
            if result is None:
                logger.info(
                    f"set_relationship：{member_id} 族譜中無 {user_id}，略過反向更新"
                )
        except Exception as e:
            logger.error(f"set_relationship：反向更新失敗 ({member_id} → {user_id}): {e}")

        return updated_tree
