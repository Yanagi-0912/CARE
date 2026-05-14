import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException

from app.services.family.family_tree_service import FamilyTreeService
from app.models.family_tree import PendingInvitation, FamilyTree, FamilyMember

@pytest.fixture
def mock_user_service():
    return AsyncMock()

@pytest.fixture
def service(mock_user_service):
    return FamilyTreeService(user_profile_service=mock_user_service)

@pytest.mark.asyncio
async def test_create_invitation(service):
    inviter_id = "U12345"
    
    with patch("app.repositories.family_tree_repository.FamilyTreeRepository.save_invitation", new_callable=AsyncMock) as mock_save:
        res = await service.create_invitation(inviter_id)
        
        assert res.invite_token is not None
        assert len(res.invite_token) > 0
        mock_save.assert_called_once()
        # 檢查呼叫參數
        args, kwargs = mock_save.call_args
        assert kwargs["inviter_id"] == inviter_id
        assert isinstance(kwargs["expires_at"], datetime)

@pytest.mark.asyncio
async def test_verify_invitation_success(service, mock_user_service):
    code = "test-token"
    inviter_id = "U_INVITER"
    expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    
    mock_invite = PendingInvitation(
        _id=code,
        inviter_id=inviter_id,
        status="pending",
        created_at=datetime.now(timezone.utc),
        expires_at=expires_at
    )
    
    with patch("app.repositories.family_tree_repository.FamilyTreeRepository.get_invitation", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_invite
        mock_user_service.get_user_profile.return_value = {"name": "測試家人"}
        
        res = await service.verify_invitation(code)
        
        assert res.inviter_display_name == "測試家人"
        assert res.expires_at == expires_at.isoformat()

@pytest.mark.asyncio
async def test_verify_invitation_expired(service):
    code = "expired-token"
    # 設定為過去的時間
    expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    
    mock_invite = PendingInvitation(
        _id=code,
        inviter_id="U1",
        status="pending",
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
        expires_at=expires_at
    )
    
    with patch("app.repositories.family_tree_repository.FamilyTreeRepository.get_invitation", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_invite
        
        with pytest.raises(HTTPException) as excinfo:
            await service.verify_invitation(code)
        assert excinfo.value.status_code == 410
        assert "已失效" in excinfo.value.detail

@pytest.mark.asyncio
async def test_accept_invitation_already_member(service):
    invitee_id = "U_ME"
    inviter_id = "U_INVITER"
    code = "join-token"
    
    mock_invite = PendingInvitation(
        _id=code,
        inviter_id=inviter_id,
        status="pending",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1)
    )
    
    # 模擬我已經在對方的族譜裡了
    mock_inviter_tree = FamilyTree(
        user_id=inviter_id,
        family_members=[FamilyMember(user_id=invitee_id, relationship_type="child")],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    
    with patch("app.repositories.family_tree_repository.FamilyTreeRepository.get_invitation", new_callable=AsyncMock) as mock_get_invite, \
         patch("app.repositories.family_tree_repository.FamilyTreeRepository.get_by_user_id", new_callable=AsyncMock) as mock_get_tree:
        
        mock_get_invite.return_value = mock_invite
        mock_get_tree.return_value = mock_inviter_tree
        
        res = await service.accept_invitation(invitee_id, code)
        
        assert res.status == "already_member"
        assert "你已是此家庭成員" in res.message

@pytest.mark.asyncio
async def test_accept_invitation_success(service):
    invitee_id = "U_NEW"
    inviter_id = "U_INVITER"
    code = "valid-token"
    
    mock_invite = PendingInvitation(
        _id=code,
        inviter_id=inviter_id,
        status="pending",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1)
    )
    
    with patch("app.repositories.family_tree_repository.FamilyTreeRepository.get_invitation", new_callable=AsyncMock) as mock_get_invite, \
         patch("app.repositories.family_tree_repository.FamilyTreeRepository.get_by_user_id", new_callable=AsyncMock) as mock_get_tree, \
         patch.object(FamilyTreeService, "add_to_family", new_callable=AsyncMock) as mock_add:
        
        mock_get_invite.return_value = mock_invite
        mock_get_tree.return_value = None # 尚未建立族譜或對方族譜為空
        
        res = await service.accept_invitation(invitee_id, code)
        
        assert res.status == "joined"
        mock_add.assert_called_once_with(invitee_id, code)
