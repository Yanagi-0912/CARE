import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException

from app.services.family.family_tree_service import FamilyTreeService
from app.models.family_tree import PendingInvitation, FamilyTree, FamilyMember

@pytest.fixture
def service():
    return FamilyTreeService()

@pytest.mark.asyncio
async def test_create_invitation(service):
    inviter_id = "U12345"
    mock_invite = PendingInvitation(
        _id="token123",
        inviter_id=inviter_id,
        status="pending",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    
    with patch("app.repositories.family_tree_repository.FamilyTreeRepository.save_invitation", new_callable=AsyncMock) as mock_save:
        mock_save.return_value = mock_invite
        res = await service.create_invitation(inviter_id)
        
        assert res.id == "token123"
        mock_save.assert_called_once()
        # 檢查呼叫參數
        args, kwargs = mock_save.call_args
        assert kwargs["inviter_id"] == inviter_id
        assert isinstance(kwargs["expires_at"], datetime)

@pytest.mark.asyncio
async def test_verify_invitation_success(service):
    code = "test-token"
    inviter_id = "U_INVITER"
    expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    
    mock_invite = PendingInvitation(
        _id=code,
        inviter_id=inviter_id,
        status="pending",
        created_at=datetime.now(timezone.utc),
        expires_at=expires_at,
        inviter_display_name="測試家人"
    )
    
    with patch("app.repositories.family_tree_repository.FamilyTreeRepository.get_invitation", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_invite
        
        res = await service.verify_invitation(code)
        
        assert res.inviter_display_name == "測試家人"
        assert res.expires_at == expires_at

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
        
        status, message = await service.accept_invitation(invitee_id, code)
        
        assert status == "already_member"
        assert "你已是此家庭成員" in message

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
         patch("app.repositories.family_tree_repository.FamilyTreeRepository.accept_invitation", new_callable=AsyncMock) as mock_claim, \
         patch.object(FamilyTreeService, "_link_members", new_callable=AsyncMock) as mock_link:
        
        mock_get_invite.return_value = mock_invite
        mock_get_tree.return_value = None # 尚未建立族譜或對方族譜為空
        mock_claim.return_value = mock_invite  # 原子搶佔成功
        
        status, message = await service.accept_invitation(invitee_id, code)
        
        assert status == "joined"
        assert message is None
        # 先搶邀請（帶上是誰接受的），再連結雙方族譜
        mock_claim.assert_awaited_once_with(code, accepted_by=invitee_id)
        mock_link.assert_awaited_once_with(inviter_id, invitee_id, None)


@pytest.mark.asyncio
async def test_accept_invitation_claimed_by_someone_else_is_410(service):
    """讀到時還是 pending、搶的時候已被搶走：當成已使用，不改族譜。"""
    code = "raced-token"
    mock_invite = PendingInvitation(
        _id=code,
        inviter_id="U_INVITER",
        status="pending",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1)
    )
    with patch("app.repositories.family_tree_repository.FamilyTreeRepository.get_invitation", new_callable=AsyncMock) as mock_get_invite, \
         patch("app.repositories.family_tree_repository.FamilyTreeRepository.get_by_user_id", new_callable=AsyncMock) as mock_get_tree, \
         patch("app.repositories.family_tree_repository.FamilyTreeRepository.accept_invitation", new_callable=AsyncMock) as mock_claim, \
         patch.object(FamilyTreeService, "_link_members", new_callable=AsyncMock) as mock_link:
        mock_get_invite.return_value = mock_invite
        mock_get_tree.return_value = None
        mock_claim.return_value = None

        with pytest.raises(HTTPException) as excinfo:
            await service.accept_invitation("U_NEW", code)
        assert excinfo.value.status_code == 410
        mock_link.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_family_tree(service):
    user_id = "U12345"
    mock_tree = FamilyTree(
        user_id=user_id,
        family_members=[
            FamilyMember(
                user_id="U67890",
                relationship_type="spouse",
                display_name="另一半",
                picture_url="https://example.com/pic.jpg",
            )
        ],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    with patch(
        "app.repositories.family_tree_repository.FamilyTreeRepository.upsert_tree",
        new_callable=AsyncMock,
    ) as mock_upsert, patch(
        "app.repositories.family_tree_repository.FamilyTreeRepository.get_by_user_id",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_upsert.return_value = mock_tree
        mock_get.return_value = mock_tree

        result = await service.get_family_tree(user_id)

        assert result == mock_tree
        mock_upsert.assert_called_once_with(user_id)
        mock_get.assert_called_once_with(user_id)


@pytest.mark.asyncio
async def test_set_relationship_updates_only_the_operators_view(service):
    user_id = "U_ME"
    member_id = "U_INVITER"
    relationship_type = "parent"
    
    mock_tree = FamilyTree(
        user_id=user_id,
        family_members=[FamilyMember(user_id=member_id, relationship_type="parent")],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    
    with patch("app.repositories.family_tree_repository.FamilyTreeRepository.set_relationship", new_callable=AsyncMock) as mock_set:
        mock_set.return_value = mock_tree
        
        result = await service.set_relationship(user_id, member_id, relationship_type)
        
        assert result == mock_tree
        mock_set.assert_called_once_with(user_id, member_id, "parent")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relationship_type",
    [
        "parent",
        "child",
        "spouse",
        "sibling",
        "grandparent",
        "grandchild",
        "other",
    ],
)
async def test_set_relationship_accepts_every_supported_label(
    service, relationship_type
):
    tree = _tree_with("U_ME", ["U_MEMBER"])
    with patch(
        "app.repositories.family_tree_repository.FamilyTreeRepository.set_relationship",
        new_callable=AsyncMock,
        return_value=tree,
    ) as mock_set:
        await service.set_relationship("U_ME", "U_MEMBER", relationship_type)

    mock_set.assert_awaited_once_with("U_ME", "U_MEMBER", relationship_type)


@pytest.mark.asyncio
async def test_set_relationship_accepts_none_to_clear_the_label(service):
    tree = _tree_with("U_ME", ["U_MEMBER"])
    with patch(
        "app.repositories.family_tree_repository.FamilyTreeRepository.set_relationship",
        new_callable=AsyncMock,
        return_value=tree,
    ) as mock_set:
        result = await service.set_relationship("U_ME", "U_MEMBER", None)

    assert result == tree
    mock_set.assert_awaited_once_with("U_ME", "U_MEMBER", None)


@pytest.mark.asyncio
async def test_set_relationship_rejects_self_as_the_target(service):
    with patch(
        "app.repositories.family_tree_repository.FamilyTreeRepository.set_relationship",
        new_callable=AsyncMock,
    ) as mock_set:
        with pytest.raises(HTTPException) as excinfo:
            await service.set_relationship("U_ME", "U_ME", "parent")

    assert excinfo.value.status_code == 400
    mock_set.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_relationship_rejects_an_unknown_label(service):
    with patch(
        "app.repositories.family_tree_repository.FamilyTreeRepository.set_relationship",
        new_callable=AsyncMock,
    ) as mock_set:
        with pytest.raises(HTTPException) as excinfo:
            await service.set_relationship("U_ME", "U_MEMBER", "friend")

    assert excinfo.value.status_code == 400
    mock_set.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_relationship_returns_not_found_for_an_unlinked_target(service):
    with patch(
        "app.repositories.family_tree_repository.FamilyTreeRepository.set_relationship",
        new_callable=AsyncMock,
        return_value=None,
    ) as mock_set:
        with pytest.raises(HTTPException) as excinfo:
            await service.set_relationship("U_ME", "U_STRANGER", "parent")

    assert excinfo.value.status_code == 404
    mock_set.assert_awaited_once_with("U_ME", "U_STRANGER", "parent")


def _tree_with(owner_id: str, member_ids: list[str]) -> FamilyTree:
    now = datetime.now(timezone.utc)
    return FamilyTree(
        user_id=owner_id,
        family_members=[FamilyMember(user_id=m) for m in member_ids],
        created_at=now,
        updated_at=now,
    )


def test_ensure_family_member_has_been_removed():
    """`ensure_family_member` 已刪除，且刻意不保留相容層。

    它的語意是「在族譜裡＝有權」——那正是本次授權改動要消滅的東西，而且它比
    權限矩陣寬。留一個相容層就會有人繼續用它，於是同一個問題有兩個答案，
    其中一個永遠是錯的。

    這四條原本驗的行為（查自己不查庫、成員放行、陌生人 403、不對稱）現在由
    tests/unit/services/family/test_family_authorization_service.py 與
    tests/unit/routers/test_endpoint_authorization.py 覆蓋，且方向是新的：
    看的是**目標擁有者**的族譜，不是請求者的。
    """
    assert not hasattr(FamilyTreeService, "ensure_family_member")


# ── is_invitation_usable ──────────────────────────────────────────────────
#
# QR 圖片端點問的是這支。它與 verify_invitation 共用同一套「可用」判定
# （FamilyTreeService._is_usable），差別只在回報方式：這支把「不存在」與
# 「已失效」壓成同一個 False，不給出可以枚舉有效邀請碼的差異。


def _invitation(*, status: str = "pending", expires_in: timedelta = timedelta(days=1)):
    return PendingInvitation(
        _id="token123",
        inviter_id="U_INVITER",
        status=status,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + expires_in,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invitation", "expected"),
    [
        (_invitation(), True),
        (_invitation(status="accepted"), False),
        (_invitation(status="revoked"), False),
        (_invitation(expires_in=timedelta(days=-1)), False),
        (None, False),
    ],
    ids=["pending", "accepted", "revoked", "expired", "missing"],
)
async def test_is_invitation_usable(service, invitation, expected):
    with patch(
        "app.repositories.family_tree_repository.FamilyTreeRepository.get_invitation",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = invitation

        assert await service.is_invitation_usable("token123") is expected


@pytest.mark.asyncio
async def test_is_invitation_usable_treats_naive_expiry_as_utc(service):
    # Mongo 取回的 datetime 多半沒有 tzinfo。當成 UTC 比較，不能讓它跟
    # aware 的 now() 相減而拋 TypeError。
    invitation = _invitation()
    invitation.expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        tzinfo=None
    )

    with patch(
        "app.repositories.family_tree_repository.FamilyTreeRepository.get_invitation",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = invitation

        assert await service.is_invitation_usable("token123") is True


@pytest.mark.asyncio
async def test_accept_and_verify_agree_on_expiry(service):
    """同一筆過期邀請，兩條路徑都要判失效。

    這兩支曾經各寫各的過期判定。只要有一邊算法不同，使用者就會看到「QR 還
    在、按下去卻 410」——而那種不一致從畫面上完全無從理解。
    """
    expired = _invitation(expires_in=timedelta(seconds=-1))

    with patch(
        "app.repositories.family_tree_repository.FamilyTreeRepository.get_invitation",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = expired

        assert await service.is_invitation_usable("token123") is False

        with pytest.raises(HTTPException) as verify_error:
            await service.verify_invitation("token123")
        with pytest.raises(HTTPException) as accept_error:
            await service.accept_invitation("U_INVITEE", "token123")

    assert verify_error.value.status_code == 410
    assert accept_error.value.status_code == 410
