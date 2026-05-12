import pytest
import os
from datetime import datetime, timezone
import logging

from app.application.family.family_tree_service import FamilyTreeService
from app.db.mongodb import MongoDBManager
from app.core.config import settings
from app.models.family_tree import REVERSE_RELATIONSHIP

logger = logging.getLogger(__name__)

@pytest.mark.integration
@pytest.mark.asyncio
class TestFamilyTreeIntegration:
    """家庭族譜整合測試：驗證與真實 MongoDB 的連動邏輯"""

    # 測試用的預設 UID
    USER_A = "test_integration_user_A"
    USER_B = "test_integration_user_B"

    @pytest.fixture(autouse=True)
    async def setup_mongodb(self):
        """每一場測試都重新建立連線，防止 Event Loop closed 問題"""
        mongodb_url = os.getenv("MONGODB_URL") or settings.MONGODB_URI
        if not mongodb_url:
            pytest.fail("未設定 MONGODB_URL（或 MONGODB_URI）環境變數，無法執行整合測試")
        
        MongoDBManager._client = None
        MongoDBManager.configure(mongodb_url)
        logger.info("Integration Test: MongoDB client reset")

        # 確保 LIFF_URL 有值，否則 FamilyTreeService.send_invitation 會拋出 500
        if not settings.LIFF_URL:
            settings.LIFF_URL = "https://liff.line.me/test-dummy-url"
            logger.info(f"Integration Test: Setting dummy LIFF_URL: {settings.LIFF_URL}")
            
        yield

    async def _cleanup(self):
        """清理測試相關的族譜與邀請記錄，避免測試環境殘留髒資料"""
        try:
            tree_col = MongoDBManager.get_family_tree_collection()
            invite_col = MongoDBManager.get_pending_invitations_collection()
            
            # 清理 Family Trees
            await tree_col.delete_many({
                "user_id": {"$in": [self.USER_A, self.USER_B]}
            })
            
            # 清理 Invitations
            await invite_col.delete_many({
                "inviter_id": {"$in": [self.USER_A, self.USER_B]}
            })
            logger.info("清理測試用 MongoDB 資料成功")
        except Exception as e:
            logger.warning(f"清理測試資料失敗: {e}")

    # ── 測試流程 ──────────────────────────────────────────────────────────────

    async def test_full_application_flow(self):
        """測試完整應用流程：發送邀請 -> 接受邀請 -> 設定關係"""
        
        # 0. 先執行首輪清理
        await self._cleanup()

        try:
            # 1. User A 發送邀請
            logger.info(f"步驟 1: {self.USER_A} 發送邀請")
            invitation_resp = await FamilyTreeService.send_invitation(self.USER_A)
            assert invitation_resp.invite_id is not None
            assert "inviteId=" in invitation_resp.invite_url
            
            # 2. User B 接受邀請
            logger.info(f"步驟 2: {self.USER_B} 接受邀請 (ID: {invitation_resp.invite_id})")
            add_resp = await FamilyTreeService.add_to_family(self.USER_B, invitation_resp.invite_id)
            assert add_resp.success is True
            
            # 驗證 A 與 B 雙方的族譜是否已初步建立
            tree_a = await FamilyTreeService.get_family_tree(self.USER_A)
            tree_b = await FamilyTreeService.get_family_tree(self.USER_B)
            
            # A 的成員清單應包含 B
            assert any(m.user_id == self.USER_B for m in tree_a.family_members), f"{self.USER_A} 的族譜應包含 {self.USER_B}"
            # B 的成員清單應包含 A
            assert any(m.user_id == self.USER_A for m in tree_b.family_members), f"{self.USER_B} 的族譜應包含 {self.USER_A}"
            
            # 3. User A 設定 User B 的關係為 'parent'
            logger.info(f"步驟 3: {self.USER_A} 設定 {self.USER_B} 為 parent")
            await FamilyTreeService.set_relationship(self.USER_A, self.USER_B, "parent")
            
            # 驗證雙向關係同步
            # A 視點: B 是 parent
            updated_tree_a = await FamilyTreeService.get_family_tree(self.USER_A)
            member_b_in_a = next(m for m in updated_tree_a.family_members if m.user_id == self.USER_B)
            assert member_b_in_a.relationship_type == "parent", "A 視點關係更新失敗"
            
            # B 視點: A 應自動成為 child (反向關係)
            updated_tree_b = await FamilyTreeService.get_family_tree(self.USER_B)
            member_a_in_b = next(m for m in updated_tree_b.family_members if m.user_id == self.USER_A)
            assert member_a_in_b.relationship_type == "child", "B 視點反向關係同步失敗"
            
            logger.info("完整流程整合測試通過！")
            
        finally:
            # 4. 測試結束不論成敗皆清理
            await self._cleanup()

    @pytest.mark.asyncio
    async def test_get_non_existent_tree(self):
        """測試取得不存在的族譜應自動建立空族譜"""
        await self._cleanup()
        
        tree = await FamilyTreeService.get_family_tree(self.USER_A)
        assert tree.user_id == self.USER_A
        assert len(tree.family_members) == 0
        
        await self._cleanup()
