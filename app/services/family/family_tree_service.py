import logging
import secrets
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException

from app.core.config import settings
from app.models.family_tree import (
    FamilyMember,
    FamilyTree,
    REVERSE_RELATIONSHIP,
    CreateInviteResponse,
    VerifyInviteResponse,
    AcceptInviteResponse,
)
from app.repositories.family_tree_repository import FamilyTreeRepository
from app.services.users.user_profile_service import UserProfileService

logger = logging.getLogger(__name__)


class FamilyTreeService:
    """家庭族譜業務邏輯層，協調 Repository 操作並處理跨成員的雙向更新。"""

    def __init__(self, user_profile_service: UserProfileService):
        self._user_profile_service = user_profile_service

    async def create_invitation(self, inviter_id: str) -> CreateInviteResponse:
        """建立邀請碼與過期時間，並存入資料庫。"""
        token = secrets.token_urlsafe(8)
        # 設定 7 天過期
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=7)

        await FamilyTreeRepository.save_invitation(
            token=token, inviter_id=inviter_id, expires_at=expires_at
        )

        logger.info(f"邀請已建立：inviter={inviter_id}, token={token}")
        return CreateInviteResponse(
            invite_token=token, expires_at=expires_at.isoformat()
        )

    async def verify_invitation(self, code: str) -> VerifyInviteResponse:
        """驗證邀請碼有效性並取得邀請者名稱。"""
        invitation = await FamilyTreeRepository.get_invitation(code)

        if invitation is None:
            raise HTTPException(status_code=404, detail="邀請連結無效")

        if invitation.status == "accepted":
            raise HTTPException(status_code=410, detail="邀請連結已失效")

        now = datetime.now(tz=timezone.utc)
        expires_at = invitation.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if now > expires_at:
            raise HTTPException(status_code=410, detail="邀請連結已失效")

        # 取得邀請者名稱
        inviter_profile = await self._user_profile_service.get_user_profile(
            invitation.inviter_id
        )
        display_name = (
            inviter_profile.get("name", "神秘家人") if inviter_profile else "神秘家人"
        )

        return VerifyInviteResponse(
            inviter_display_name=display_name, expires_at=expires_at.isoformat()
        )

    async def accept_invitation(
        self, invitee_id: str, code: str
    ) -> AcceptInviteResponse:
        """接受邀請並加入家族，處理 already_member 情況。"""
        invitation = await FamilyTreeRepository.get_invitation(code)

        if invitation is None:
            raise HTTPException(status_code=404, detail="邀請連結無效")

        if invitation.status == "accepted" or invitation.expires_at.replace(
            tzinfo=timezone.utc
        ) < datetime.now(tz=timezone.utc):
            raise HTTPException(status_code=410, detail="邀請連結已失效")

        inviter_id = invitation.inviter_id
        if inviter_id == invitee_id:
            raise HTTPException(status_code=400, detail="無法邀請自己加入族譜")

        # 檢查是否已是成員
        inviter_tree = await FamilyTreeRepository.get_by_user_id(inviter_id)
        if inviter_tree and any(
            m.user_id == invitee_id for m in inviter_tree.family_members
        ):
            return AcceptInviteResponse(
                status="already_member", message="你已是此家庭成員"
            )

        # 執行原本的加入邏輯
        await self.add_to_family(invitee_id, code)

        return AcceptInviteResponse(status="joined")

    async def add_to_family(self, invitee_id: str, invite_id: str) -> None:
        """
        將 invitee 與 inviter 雙向加入彼此的族譜。
        雙向寫入採 best-effort：若其中一邊失敗則 log 後繼續，不回滾。
        """
        invitation = await FamilyTreeRepository.get_invitation(invite_id)
        if not invitation:
            return

        inviter_id = invitation.inviter_id

        # 確保雙方族譜存在（upsert）
        await FamilyTreeRepository.upsert_tree(inviter_id)
        await FamilyTreeRepository.upsert_tree(invitee_id)

        # 1. inviter 的族譜加入 invitee
        await FamilyTreeRepository.add_member(
            inviter_id, FamilyMember(user_id=invitee_id)
        )

        # 2. invitee 的族譜加入 inviter
        await FamilyTreeRepository.add_member(
            invitee_id, FamilyMember(user_id=inviter_id)
        )

        # 3. 標記邀請為已使用
        await FamilyTreeRepository.accept_invitation(invite_id)
        logger.info(f"成員加入成功：inviter={inviter_id}, invitee={invitee_id}")

    # ── 取得族譜 ──────────────────────────────────────────────────────────────

    async def get_family_tree(self, user_id: str) -> FamilyTree:
        """取得族譜；若尚不存在則建立空族譜並回傳。"""
        return await FamilyTreeRepository.upsert_tree(user_id)

    # ── 設定關係類型（雙向）────────────────────────────────────────────────────

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
            logger.error(
                f"set_relationship：反向更新失敗 ({member_id} → {user_id}): {e}"
            )

        return updated_tree
