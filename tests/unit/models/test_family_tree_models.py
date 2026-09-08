"""族譜模型的角色欄位與遷移狀態。

重點在「未設定」與「明確設定為 MEMBER」的區分：兩者在授權上等價，在
「擁有者是否已完成引導式指派」的判定上不等價。這個區分由欄位的有無承載，
所以模型層必須保住 `None` 這個狀態，不能用預設值把它抹掉。
"""

import pytest
from pydantic import ValidationError

from app.models.family_tree import (
    FamilyMember,
    FamilyRoleEntry,
    FamilyTree,
    SetFamilyRoleRequest,
)


def _tree(**kwargs) -> FamilyTree:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return FamilyTree(user_id="U-owner", created_at=now, updated_at=now, **kwargs)


def test_family_role_absent_means_unset_not_member():
    """既有文件沒有這個欄位，讀回時 SHALL 是 None——不是 MEMBER。

    若模型把預設值填成 MEMBER，「未設定」這個狀態就消失了，引導式指派的
    完成判定會在擁有者什麼都沒做的情況下回報「已完成」。
    """
    member = FamilyMember(user_id="U1")
    assert member.family_role is None
    assert member.effective_family_role == "MEMBER"


def test_explicit_member_is_distinguishable_from_unset():
    explicit = FamilyMember(user_id="U1", family_role="MEMBER")
    unset = FamilyMember(user_id="U2")
    assert explicit.family_role == "MEMBER"
    assert unset.family_role is None
    # 授權上兩者等價
    assert explicit.effective_family_role == unset.effective_family_role


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER", "MEMBER"])
def test_assignable_roles_accepted(role):
    assert FamilyMember(user_id="U1", family_role=role).family_role == role


def test_owner_cannot_be_assigned_to_a_member():
    """OWNER 是推導值，寫入它等於讓渡資料所有權。"""
    with pytest.raises(ValidationError):
        FamilyMember(user_id="U1", family_role="OWNER")


def test_set_role_request_accepts_raw_string_so_service_can_return_400():
    """請求模型刻意寬鬆：spec 要求指派 OWNER 回 400，而型別檢查會回 422。

    值的檢查留給服務層，狀態碼才對得上，訊息也才講得出「OWNER 不是可指派的
    角色」而不是一句 literal 不匹配。
    """
    assert SetFamilyRoleRequest(family_role="OWNER").family_role == "OWNER"


def test_unknown_role_rejected():
    with pytest.raises(ValidationError):
        FamilyMember(user_id="U1", family_role="ADMIN")


def test_tree_defaults_to_shadow_migration_state():
    """預設不強制：既有家庭不會因為部署就突然失去功能。"""
    assert _tree().rbac_migration_state == "shadow"


def test_tree_migration_state_is_per_owner():
    """狀態存在擁有者的文件上，因此不同擁有者可以各自處於不同狀態。"""
    shadow = _tree()
    enforced = _tree()
    enforced.rbac_migration_state = "enforced"
    assert shadow.rbac_migration_state == "shadow"
    assert enforced.rbac_migration_state == "enforced"


def test_invalid_migration_state_rejected():
    with pytest.raises(ValidationError):
        FamilyTree(
            user_id="U-owner",
            rbac_migration_state="legacy",
            created_at=__import__("datetime").datetime.now(),
            updated_at=__import__("datetime").datetime.now(),
        )


def test_role_entry_keeps_unset_visible_to_presentation():
    """呈現面要能分辨「未設定」與「設成 MEMBER」，才講得出正確的話。

    直接把未設定顯示成 MEMBER，擁有者會以為自己已經設定過了——而 spec 要求
    介面明確告知「未設定者將以 MEMBER 權限處理」。
    """
    entry = FamilyRoleEntry(user_id="U1")
    assert entry.family_role is None
    assert entry.effective_family_role == "MEMBER"


def test_care_recipient_flag_is_independent_of_role():
    """照顧對象標記是業務狀態，與角色互不推導。"""
    member = FamilyMember(user_id="U1", is_care_recipient=True)
    assert member.family_role is None
    guardian = FamilyMember(user_id="U2", family_role="GUARDIAN")
    assert guardian.is_care_recipient is False
