import logging
import secrets
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException

from app.core.config import settings
from app.models.family_tree import (
    FamilyMember,
    FamilyTree,
    REVERSE_RELATIONSHIP,
    PendingInvitation,
)
from app.repositories.family_tree_repository import FamilyTreeRepository
logger = logging.getLogger(__name__)


class FamilyTreeService:
    """
    家庭服務功能。
    """

    async def get_family_tree(self, user_id: str) -> FamilyTree:
        """
        取得族譜，若尚不存在則建立空族譜並回傳。
        使用 MongoDB Aggregation $lookup 進行資料庫關聯查詢以確保效能與實時更新。
        """
        await FamilyTreeRepository.upsert_tree(user_id)
        tree = await FamilyTreeRepository.get_by_user_id(user_id)
        assert tree is not None
        return tree

    async def ensure_family_member(self, requester_id: str, target_id: str) -> None:
        """確認 requester_id 有權讀取 target_id 的資料，否則丟 403。

        規則只有兩條：查自己一律放行；查別人則對方必須在自己的族譜成員名單內。
        任何以 /{userId} 形式讀取他人資料的端點都該先過這一關——光有登入態不代表
        有權讀取任意 userId 的資料，否則只要知道對方的 LINE userId 就能撈走。
        """
        if requester_id == target_id:
            return

        tree = await self.get_family_tree(requester_id)
        if not any(m.user_id == target_id for m in tree.family_members):
            raise HTTPException(
                status_code=403,
                detail="您無權查看此使用者的資料（非家庭成員）",
            )

    async def create_invitation(self, inviter_id: str) -> PendingInvitation:
        """
        建立邀請碼與過期時間，並存入資料庫。
        """
        token = secrets.token_urlsafe(8)
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=7)

        invitation = await FamilyTreeRepository.save_invitation(
            token=token, inviter_id=inviter_id, expires_at=expires_at
        )

        logger.info(f"邀請已建立：inviter={inviter_id}, token={token}")
        return invitation

    async def verify_invitation(self, code: str) -> PendingInvitation:
        """
        驗證邀請碼並取得邀請者名稱。
        """
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

        return invitation

    async def accept_invitation(
        self, invitee_id: str, code: str
    ) -> tuple[str, str | None]:
        """
        接受邀請並加入家族，處理 already_member 情況。
        回傳 tuple (status, message)。
        """
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

        inviter_tree = await FamilyTreeRepository.get_by_user_id(inviter_id)
        if inviter_tree and any(
            m.user_id == invitee_id for m in inviter_tree.family_members
        ):
            return "already_member", "你已是此家庭成員"

        await self.add_to_family(invitee_id, code)

        return "joined", None

    async def add_to_family(self, invitee_id: str, invite_id: str) -> None:
        """
        將家人加入家庭。
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
        updated_tree = await FamilyTreeRepository.set_relationship(
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
        updated_tree = await FamilyTreeRepository.set_care_recipient(
            user_id, member_id, is_care_recipient
        )
        if updated_tree is None:
            raise HTTPException(
                status_code=404,
                detail=f"在 {user_id} 的族譜中找不到成員 {member_id}",
            )
        return updated_tree

