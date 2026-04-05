import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException

from app.application.family.family_tree_service import FamilyTreeService
from app.models.family_tree import (
    FamilyTree,
    FamilyMember,
    PendingInvitation,
    SendInvitationResponse,
    AddToFamilyResponse,
)

# ── 測試設定 ──────────────────────────────────────────────────────────────

@pytest.fixture
def mock_now():
    return datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

@pytest.fixture
def sample_tree(mock_now):
    return FamilyTree(
        user_id="user_123",
        family_members=[],
        created_at=mock_now,
        updated_at=mock_now
    )

@pytest.fixture
def sample_invitation(mock_now):
    return PendingInvitation(
        _id="invite_888",
        inviter_id="user_123",
        status="pending",
        created_at=mock_now,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(days=7)
    )

# ── 發送邀請 ──────────────────────────────────────────────────────────────

class TestSendInvitation:
    """測試發送邀請邏輯"""

    @pytest.mark.asyncio
    @patch("app.application.family.family_tree_service.FamilyTreeRepository")
    @patch("app.application.family.family_tree_service.settings")
    async def test_send_invitation_success(self, mock_settings, mock_repo, sample_invitation):
        # 設定 Mock
        mock_repo.create_invitation = AsyncMock(return_value=sample_invitation)
        mock_settings.LIFF_URL = "https://liff.line.me/test-app"
        
        # 執行
        result = await FamilyTreeService.send_invitation("user_123")
        
        # 驗證
        assert isinstance(result, SendInvitationResponse)
        assert result.invite_id == "invite_888"
        assert result.invite_url == "https://liff.line.me/test-app?inviteId=invite_888"
        mock_repo.create_invitation.assert_called_once_with("user_123")

    @pytest.mark.asyncio
    @patch("app.application.family.family_tree_service.FamilyTreeRepository")
    @patch("app.application.family.family_tree_service.settings")
    async def test_send_invitation_no_liff_url(self, mock_settings, mock_repo, sample_invitation):
        # 設定 Mock
        mock_repo.create_invitation = AsyncMock(return_value=sample_invitation)
        mock_settings.LIFF_URL = "" # 未設定
        
        # 執行並驗證異常
        with pytest.raises(HTTPException) as exc:
            await FamilyTreeService.send_invitation("user_123")
        
        assert exc.value.status_code == 500
        assert "LIFF_URL is not configured" in exc.value.detail

# ── 接受邀請 ──────────────────────────────────────────────────────────────

class TestAddToFamily:
    """測試接受邀請並加入族譜邏輯"""

    @pytest.mark.asyncio
    @patch("app.application.family.family_tree_service.FamilyTreeRepository")
    async def test_add_to_family_success(self, mock_repo, sample_invitation, sample_tree, mock_now):
        # 設定 Mock
        mock_repo.get_invitation = AsyncMock(return_value=sample_invitation)
        mock_repo.upsert_tree = AsyncMock()
        mock_repo.add_member = AsyncMock()
        mock_repo.accept_invitation = AsyncMock()
        
        # 模擬回傳 invitee 最新的 Tree
        invitee_tree = sample_tree.model_copy()
        invitee_tree.user_id = "invitee_456"
        invitee_tree.family_members = [FamilyMember(user_id="user_123")]
        mock_repo.get_by_user_id = AsyncMock(return_value=invitee_tree)
        
        # 執行
        result = await FamilyTreeService.add_to_family("invitee_456", "invite_888")
        
        # 驗證
        assert result.success is True
        assert result.family_tree.user_id == "invitee_456"
        assert len(result.family_tree.family_members) == 1
        
        # 驗證雙向寫入是否有被呼叫
        assert mock_repo.upsert_tree.call_count == 2
        assert mock_repo.add_member.call_count == 2
        mock_repo.accept_invitation.assert_called_once_with("invite_888")

    @pytest.mark.asyncio
    @patch("app.application.family.family_tree_service.FamilyTreeRepository")
    async def test_add_to_family_not_found(self, mock_repo):
        mock_repo.get_invitation = AsyncMock(return_value=None)
        
        with pytest.raises(HTTPException) as exc:
            await FamilyTreeService.add_to_family("invitee_456", "wrong_id")
        
        assert exc.value.status_code == 404
        assert "邀請不存在" in exc.value.detail

    @pytest.mark.asyncio
    @patch("app.application.family.family_tree_service.FamilyTreeRepository")
    async def test_add_to_family_already_accepted(self, mock_repo, sample_invitation):
        sample_invitation.status = "accepted"
        mock_repo.get_invitation = AsyncMock(return_value=sample_invitation)
        
        with pytest.raises(HTTPException) as exc:
            await FamilyTreeService.add_to_family("invitee_456", "invite_888")
        
        assert exc.value.status_code == 409
        assert "此邀請已被使用" in exc.value.detail

    @pytest.mark.asyncio
    @patch("app.application.family.family_tree_service.FamilyTreeRepository")
    async def test_add_to_family_expired(self, mock_repo, sample_invitation):
        # 設定為過去的時間
        sample_invitation.expires_at = datetime.now(tz=timezone.utc) - timedelta(days=1)
        mock_repo.get_invitation = AsyncMock(return_value=sample_invitation)
        
        with pytest.raises(HTTPException) as exc:
            await FamilyTreeService.add_to_family("invitee_456", "invite_888")
        
        assert exc.value.status_code == 410
        assert "邀請連結已過期" in exc.value.detail

    @pytest.mark.asyncio
    @patch("app.application.family.family_tree_service.FamilyTreeRepository")
    async def test_add_to_family_self_invite(self, mock_repo, sample_invitation):
        mock_repo.get_invitation = AsyncMock(return_value=sample_invitation)
        
        with pytest.raises(HTTPException) as exc:
            # inviter 是 user_123，invitee 也是 user_123
            await FamilyTreeService.add_to_family("user_123", "invite_888")
        
        assert exc.value.status_code == 400
        assert "無法邀請自己" in exc.value.detail

    @pytest.mark.asyncio
    @patch("app.application.family.family_tree_service.FamilyTreeRepository")
    async def test_add_to_family_best_effort_error(self, mock_repo, sample_invitation, sample_tree):
        """測試 Best-effort：即使一方更新失敗也不中斷"""
        mock_repo.get_invitation = AsyncMock(return_value=sample_invitation)
        mock_repo.upsert_tree = AsyncMock()
        mock_repo.accept_invitation = AsyncMock()
        
        # 模擬 inviter 端點失敗
        mock_repo.add_member = AsyncMock(side_effect=[Exception("DB Error"), None])
        
        mock_repo.get_by_user_id = AsyncMock(return_value=sample_tree)
        
        # 執行應成功（因為有 try-except 攔截）
        result = await FamilyTreeService.add_to_family("invitee_456", "invite_888")
        assert result.success is True
        assert mock_repo.add_member.call_count == 2
        mock_repo.accept_invitation.assert_called_once()

# ── 設定關係 ──────────────────────────────────────────────────────────────

class TestSetRelationship:
    """測試設定關係邏輯"""

    @pytest.mark.asyncio
    @patch("app.application.family.family_tree_service.FamilyTreeRepository")
    async def test_set_relationship_success(self, mock_repo, sample_tree):
        # 1. 更新自身
        mock_repo.set_relationship = AsyncMock(side_effect=[sample_tree, sample_tree])
        
        # 執行 (設定為 parent，則反邊應為 child)
        await FamilyTreeService.set_relationship("user_123", "member_456", "parent")
        
        # 驗證
        assert mock_repo.set_relationship.call_count == 2
        # 檢查參數：第二次呼叫應為反向關係
        args_first = mock_repo.set_relationship.call_args_list[0][0]
        args_second = mock_repo.set_relationship.call_args_list[1][0]
        
        assert args_first == ("user_123", "member_456", "parent")
        assert args_second == ("member_456", "user_123", "child")

    @pytest.mark.asyncio
    async def test_set_relationship_invalid_type(self):
        with pytest.raises(HTTPException) as exc:
            await FamilyTreeService.set_relationship("u1", "m1", "alien")
        
        assert exc.value.status_code == 400
        assert "不支援的關係類型" in exc.value.detail

    @pytest.mark.asyncio
    @patch("app.application.family.family_tree_service.FamilyTreeRepository")
    async def test_set_relationship_not_found(self, mock_repo):
        mock_repo.set_relationship = AsyncMock(return_value=None)
        
        with pytest.raises(HTTPException) as exc:
            await FamilyTreeService.set_relationship("u1", "m1", "parent")
        
        assert exc.value.status_code == 404
        assert "找不到成員" in exc.value.detail

    @pytest.mark.asyncio
    @patch("app.application.family.family_tree_service.FamilyTreeRepository")
    async def test_set_relationship_reverse_best_effort(self, mock_repo, sample_tree):
        """測試反向更新失敗（或找不到人）不影響回傳"""
        # 第一次更新成功，第二次（反向）回傳 None
        mock_repo.set_relationship = AsyncMock(side_effect=[sample_tree, None])
        
        result = await FamilyTreeService.set_relationship("u1", "m1", "parent")
        
        assert result == sample_tree
        assert mock_repo.set_relationship.call_count == 2
