"""FamilyAuthorizationService 的窮舉判定、委任解析與 fail-closed 遮蔽。

矩陣本身已在 tests/unit/models/test_family_authorization.py 逐格驗過，這裡測的
是**服務怎麼用那張表**：角色從哪裡解析出來、委任如何介入、影子模式怎麼放行、
以及遮蔽會不會漏。

依賴一律以 fake 物件注入（openspec/config.yaml 的規則：不得使用 monkey patch）。
兩個 fake 都刻意做得很笨——它們只回傳被餵進去的資料，沒有任何判斷邏輯，
否則測試就會變成拿一份實作跟另一份實作比。
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
from fastapi import HTTPException

from app.models.family_tree import FamilyDelegation, FamilyMember, FamilyTree
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
OWNER = "U-owner"
OTHER_OWNER = "U-other-owner"
OPERATOR = "U-operator"
STRANGER = "U-stranger"


class FakeTreeRepository:
    """以 user_id 為鍵的族譜集合。查無此人回 None，與真實 repository 一致。"""

    def __init__(self, trees: Optional[dict] = None):
        self.trees = trees or {}

    async def get_by_user_id(self, user_id: str):
        return self.trees.get(user_id)


class FakeDelegationRepository:
    """只認「有效」的委任。

    刻意把有效性判斷放在這裡，與真實 repository 相同——真實實作是在 Mongo
    查詢條件裡篩掉 revoked/expired 的，服務層拿到的清單就是可以直接用的。
    測試若把過期判斷搬進服務層，就測不到真正的分工。
    """

    def __init__(self, delegations: Optional[list] = None):
        self.delegations = delegations or []

    async def has_active_delegation(self, owner_id, delegate_user_id, now=None):
        moment = now or NOW
        return any(
            d.owner_id == owner_id
            and d.delegate_user_id == delegate_user_id
            and d.is_active_at(moment)
            for d in self.delegations
        )


def make_tree(owner_id: str, members, state: str = "shadow") -> FamilyTree:
    return FamilyTree(
        user_id=owner_id,
        family_members=members,
        rbac_migration_state=state,
        created_at=NOW,
        updated_at=NOW,
    )


def make_service(
    trees=None, delegations=None, enforcement_enabled: bool = True
) -> FamilyAuthorizationService:
    return FamilyAuthorizationService(
        family_tree_repository=FakeTreeRepository(trees or {}),
        delegation_repository=FakeDelegationRepository(delegations or []),
        enforcement_enabled=enforcement_enabled,
    )


def service_with_role(role: Optional[str], state: str = "enforced"):
    """建一個「OPERATOR 對 OWNER 是 role」的服務。role=None 代表不是家人。"""
    members = [] if role is None else [FamilyMember(user_id=OPERATOR, family_role=role)]
    return make_service({OWNER: make_tree(OWNER, members, state=state)})


# ── 角色解析 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_operator_is_owner_of_own_data():
    """一個人對自己資料的權限不需要任何人授予。"""
    service = make_service({})
    assert await service.resolve_role(OWNER, OWNER) == "OWNER"


@pytest.mark.asyncio
async def test_own_data_resolution_does_not_touch_the_database():
    """自己的資料不該因為族譜讀取失敗而變成無權。"""

    class ExplodingRepository:
        async def get_by_user_id(self, user_id):  # pragma: no cover - 不該被呼叫
            raise AssertionError("解析自己的角色時不應查詢族譜")

    service = FamilyAuthorizationService(
        family_tree_repository=ExplodingRepository(),
        delegation_repository=FakeDelegationRepository(),
        enforcement_enabled=True,
    )
    assert await service.resolve_role(OWNER, OWNER) == "OWNER"


@pytest.mark.asyncio
async def test_non_member_resolves_to_none():
    service = make_service({OWNER: make_tree(OWNER, [])})
    assert await service.resolve_role(STRANGER, OWNER) is None


@pytest.mark.asyncio
async def test_missing_tree_resolves_to_none():
    """族譜不存在時 SHALL NOT 落回任何角色。"""
    service = make_service({})
    assert await service.resolve_role(OPERATOR, OWNER) is None


@pytest.mark.asyncio
async def test_absent_family_role_resolves_to_member():
    service = make_service(
        {OWNER: make_tree(OWNER, [FamilyMember(user_id=OPERATOR)])}
    )
    assert await service.resolve_role(OPERATOR, OWNER) == "MEMBER"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER", "MEMBER"])
async def test_assigned_role_is_resolved(role):
    service = service_with_role(role)
    assert await service.resolve_role(OPERATOR, OWNER) == role


@pytest.mark.asyncio
async def test_role_is_a_property_of_the_pair_not_the_operator():
    """同一個人對甲是 GUARDIAN、對乙可以是 MEMBER。"""
    service = make_service(
        {
            OWNER: make_tree(
                OWNER, [FamilyMember(user_id=OPERATOR, family_role="GUARDIAN")]
            ),
            OTHER_OWNER: make_tree(
                OTHER_OWNER, [FamilyMember(user_id=OPERATOR, family_role="MEMBER")]
            ),
        }
    )
    assert await service.resolve_role(OPERATOR, OWNER) == "GUARDIAN"
    assert await service.resolve_role(OPERATOR, OTHER_OWNER) == "MEMBER"


# ── 矩陣套用（四種角色 × 三分類 × 讀寫）──────────────────────────────

EXPECTED = {
    "OWNER": {"GENERAL": {"READ", "WRITE"}, "SENSITIVE": {"READ", "WRITE"}, "PRIVATE": {"READ", "WRITE"}},
    "GUARDIAN": {"GENERAL": {"READ", "WRITE"}, "SENSITIVE": {"READ", "WRITE"}, "PRIVATE": {"READ"}},
    "CAREGIVER": {"GENERAL": {"READ", "WRITE"}, "SENSITIVE": {"READ"}, "PRIVATE": set()},
    "MEMBER": {"GENERAL": {"READ"}, "SENSITIVE": set(), "PRIVATE": set()},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER", "MEMBER"])
@pytest.mark.parametrize("classification", ["GENERAL", "SENSITIVE", "PRIVATE"])
@pytest.mark.parametrize("action", ["READ", "WRITE"])
async def test_can_matches_matrix_for_other_peoples_data(role, classification, action):
    service = service_with_role(role)
    expected = action in EXPECTED[role][classification]
    assert await service.can(OPERATOR, OWNER, classification, action) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("classification", ["GENERAL", "SENSITIVE", "PRIVATE"])
@pytest.mark.parametrize("action", ["READ", "WRITE"])
async def test_owner_can_do_everything_to_own_data(classification, action):
    service = make_service({})
    assert await service.can(OWNER, OWNER, classification, action) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("classification", ["GENERAL", "SENSITIVE", "PRIVATE"])
@pytest.mark.parametrize("action", ["READ", "WRITE"])
async def test_stranger_can_do_nothing(classification, action):
    """不是家人就沒有任何權限——family boundary 是最外層的閘門。"""
    service = service_with_role(None)
    assert await service.can(STRANGER, OWNER, classification, action) is False


@pytest.mark.asyncio
async def test_own_write_is_not_reduced_by_role_in_someone_elses_tree():
    """他人族譜裡的角色 SHALL NOT 降低操作者對自己資料的權限。"""
    service = make_service(
        {
            OWNER: make_tree(
                OWNER, [FamilyMember(user_id=OPERATOR, family_role="MEMBER")]
            ),
            OPERATOR: make_tree(OPERATOR, []),
        }
    )
    assert await service.can(OPERATOR, OPERATOR, "SENSITIVE", "WRITE") is True


@pytest.mark.asyncio
async def test_proxy_write_does_not_reach_a_third_party():
    """GUARDIAN 的 Write 只及於授權他的那位擁有者。

    他在長輩族譜裡是 GUARDIAN，但在同族譜另一位成員的族譜裡只是 MEMBER——
    對那個人的資料就沒有寫入權。這條規則在方向 B 下是自動成立的（角色存在
    被存取者的族譜裡），這個測試釘住「自動成立」這件事本身。
    """
    third_party = "U-sibling"
    service = make_service(
        {
            OWNER: make_tree(
                OWNER,
                [
                    FamilyMember(user_id=OPERATOR, family_role="GUARDIAN"),
                    FamilyMember(user_id=third_party, family_role="MEMBER"),
                ],
            ),
            third_party: make_tree(
                third_party, [FamilyMember(user_id=OPERATOR, family_role="MEMBER")]
            ),
        }
    )
    assert await service.can(OPERATOR, OWNER, "GENERAL", "WRITE") is True
    assert await service.can(OPERATOR, third_party, "GENERAL", "WRITE") is False


# ── 委任 ──────────────────────────────────────────────────────────────


def delegation(*, expires_in_days: int = 90, revoked: bool = False) -> FamilyDelegation:
    return FamilyDelegation(
        owner_id=OWNER,
        delegate_user_id=OPERATOR,
        granted_at=NOW,
        granted_by="U-approver",
        expires_at=NOW + timedelta(days=expires_in_days),
        revoked_at=NOW if revoked else None,
    )


@pytest.mark.asyncio
async def test_active_delegation_grants_guardian_data_permissions():
    service = make_service(
        {OWNER: make_tree(OWNER, [FamilyMember(user_id=OPERATOR, family_role="MEMBER")])},
        [delegation()],
    )
    assert await service.resolve_role(OPERATOR, OWNER, now=NOW) == "GUARDIAN"
    assert await service.can(OPERATOR, OWNER, "SENSITIVE", "WRITE", now=NOW) is True


@pytest.mark.asyncio
async def test_delegation_never_resolves_to_owner():
    """擁有權不轉移：委任給的是 GUARDIAN 的權限，不多不少。"""
    service = make_service(
        {OWNER: make_tree(OWNER, [FamilyMember(user_id=OPERATOR)])},
        [delegation()],
    )
    assert await service.resolve_role(OPERATOR, OWNER, now=NOW) == "GUARDIAN"
    # GUARDIAN 對 PRIVATE 只有讀——委任 SHALL NOT 改變這一格
    assert await service.can(OPERATOR, OWNER, "PRIVATE", "WRITE", now=NOW) is False
    assert await service.can(OPERATOR, OWNER, "PRIVATE", "READ", now=NOW) is True


@pytest.mark.asyncio
async def test_expired_delegation_does_not_authorize():
    service = make_service(
        {OWNER: make_tree(OWNER, [FamilyMember(user_id=OPERATOR, family_role="MEMBER")])},
        [delegation(expires_in_days=90)],
    )
    later = NOW + timedelta(days=91)
    assert await service.resolve_role(OPERATOR, OWNER, now=later) == "MEMBER"
    assert await service.can(OPERATOR, OWNER, "SENSITIVE", "READ", now=later) is False


@pytest.mark.asyncio
async def test_revoked_delegation_does_not_authorize():
    service = make_service(
        {OWNER: make_tree(OWNER, [FamilyMember(user_id=OPERATOR, family_role="MEMBER")])},
        [delegation(revoked=True)],
    )
    assert await service.resolve_role(OPERATOR, OWNER, now=NOW) == "MEMBER"
    assert await service.can(OPERATOR, OWNER, "SENSITIVE", "READ", now=NOW) is False


@pytest.mark.asyncio
async def test_delegation_expiry_does_not_erase_assigned_role():
    """失效只收回「代擁有者行事」，不動族譜裡本來就有的角色。

    受委任者可能同時是擁有者親自指派的 GUARDIAN，那與委任是兩回事。
    """
    service = make_service(
        {
            OWNER: make_tree(
                OWNER, [FamilyMember(user_id=OPERATOR, family_role="GUARDIAN")]
            )
        },
        [delegation(expires_in_days=1)],
    )
    later = NOW + timedelta(days=2)
    assert await service.resolve_role(OPERATOR, OWNER, now=later) == "GUARDIAN"


@pytest.mark.asyncio
async def test_delegation_does_not_bypass_family_boundary():
    """不在族譜內的人即使有委任紀錄也不通過——family boundary 是最外層閘門。"""
    service = make_service({OWNER: make_tree(OWNER, [])}, [delegation()])
    assert await service.resolve_role(OPERATOR, OWNER, now=NOW) is None


@pytest.mark.asyncio
async def test_is_active_delegate_is_separate_from_role():
    """資料權限與「能不能代擁有者行事」是兩個問題。

    擁有者親自指派的 GUARDIAN 與受委任的 GUARDIAN 在矩陣上完全相同，但只有
    後者能代為指派角色、也只有前者所在的擁有者能授予 GUARDIAN。兩者若從同一
    個回傳值推導，就會直接產生一條提權路徑。
    """
    assigned = make_service(
        {OWNER: make_tree(OWNER, [FamilyMember(user_id=OPERATOR, family_role="GUARDIAN")])}
    )
    delegated = make_service(
        {OWNER: make_tree(OWNER, [FamilyMember(user_id=OPERATOR, family_role="MEMBER")])},
        [delegation()],
    )
    assert await assigned.resolve_role(OPERATOR, OWNER) == "GUARDIAN"
    assert await assigned.is_active_delegate(OPERATOR, OWNER) is False
    assert await delegated.resolve_role(OPERATOR, OWNER, now=NOW) == "GUARDIAN"
    assert await delegated.is_active_delegate(OPERATOR, OWNER, now=NOW) is True


@pytest.mark.asyncio
async def test_owner_is_never_their_own_delegate():
    service = make_service({}, [delegation()])
    assert await service.is_active_delegate(OWNER, OWNER, now=NOW) is False


# ── authorize：影子模式與強制 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_enforced_owner_rejects_member_reading_sensitive():
    service = service_with_role("MEMBER", state="enforced")
    with pytest.raises(HTTPException) as exc:
        await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ")
    assert exc.value.status_code == 403
    assert "權限不足" in exc.value.detail


@pytest.mark.asyncio
async def test_shadow_owner_allows_member_reading_sensitive():
    """影子模式下行為與導入前完全相同：在族譜裡就放行。"""
    service = service_with_role("MEMBER", state="shadow")
    assert await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ") == "MEMBER"


@pytest.mark.asyncio
async def test_global_kill_switch_overrides_owner_state():
    """全域關閉時，即使該擁有者已標為 enforced 也不強制。"""
    service = FamilyAuthorizationService(
        family_tree_repository=FakeTreeRepository(
            {
                OWNER: make_tree(
                    OWNER,
                    [FamilyMember(user_id=OPERATOR, family_role="MEMBER")],
                    state="enforced",
                )
            }
        ),
        delegation_repository=FakeDelegationRepository(),
        enforcement_enabled=False,
    )
    assert await service.migration_state(OWNER) == "shadow"
    assert await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ") == "MEMBER"


@pytest.mark.asyncio
async def test_migration_state_follows_target_not_operator():
    """狀態綁目標擁有者：要保護的是資料，不是使用者。

    同一位操作者對已強制的甲受矩陣約束，對仍在影子的乙照舊放行。
    """
    service = make_service(
        {
            OWNER: make_tree(
                OWNER,
                [FamilyMember(user_id=OPERATOR, family_role="MEMBER")],
                state="enforced",
            ),
            OTHER_OWNER: make_tree(
                OTHER_OWNER,
                [FamilyMember(user_id=OPERATOR, family_role="MEMBER")],
                state="shadow",
            ),
        }
    )
    with pytest.raises(HTTPException):
        await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ")
    assert await service.authorize(OPERATOR, OTHER_OWNER, "SENSITIVE", "READ") == "MEMBER"


@pytest.mark.asyncio
async def test_shadow_still_rejects_non_family_member():
    """影子模式放寬的是角色，不是家庭邊界——非家人一律拒絕，與導入前一致。"""
    service = service_with_role(None, state="shadow")
    with pytest.raises(HTTPException):
        await service.authorize(STRANGER, OWNER, "GENERAL", "READ")


@pytest.mark.asyncio
async def test_new_path_without_legacy_equivalent_is_always_enforced():
    """本 change 新增的路徑不受影子模式放寬。

    健康資料的代理寫入在導入前根本不存在，「與導入前相同」的意思是**沒有這個
    能力**。若沿用 legacy 判定，影子模式會讓一位 MEMBER 取得他在強制後反而
    沒有的寫入權——那不是保留既有行為，是憑空發明一個更寬的行為。
    """
    service = service_with_role("MEMBER", state="shadow")
    with pytest.raises(HTTPException):
        await service.authorize(
            OPERATOR, OWNER, "SENSITIVE", "WRITE", has_legacy_equivalent=False
        )


@pytest.mark.asyncio
async def test_new_path_allows_guardian_even_in_shadow():
    service = service_with_role("GUARDIAN", state="shadow")
    assert (
        await service.authorize(
            OPERATOR, OWNER, "SENSITIVE", "WRITE", has_legacy_equivalent=False
        )
        == "GUARDIAN"
    )


@pytest.mark.asyncio
async def test_tighten_diff_is_recorded_in_shadow(caplog):
    import logging

    service = service_with_role("MEMBER", state="shadow")
    with caplog.at_level(logging.INFO):
        await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ")
    assert any("family_rbac_migration_diff" in r.message % r.args for r in caplog.records)
    assert any("tighten" in str(r.args) for r in caplog.records)


@pytest.mark.asyncio
async def test_no_diff_recorded_when_both_agree(caplog):
    import logging

    service = service_with_role("GUARDIAN", state="shadow")
    with caplog.at_level(logging.INFO):
        await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ")
    assert not any("family_rbac_migration_diff" in str(r.args) for r in caplog.records)


@pytest.mark.asyncio
async def test_loosen_diff_is_logged_at_error_level(caplog):
    """RBAC 比 legacy 寬鬆代表角色解析或矩陣有錯，是 bug 訊號不是遷移資訊。

    這裡用一個不可能自然出現的狀態構造它：操作者不在族譜（legacy 拒絕），
    但持有有效委任。真實實作的 resolve_role 會因為 family boundary 先擋下，
    所以要用一個直接回傳角色的替身把那道閘門繞開，才驗得到記錄行為本身。
    """
    import logging

    class LooseService(FamilyAuthorizationService):
        async def _resolve_context(self, operator_id, target_owner_id, now=None):
            # 角色解析出 GUARDIAN，但 legacy 判定為「不是家人」。這個組合在
            # 真實實作裡不可能出現（family boundary 會先擋下），必須用替身
            # 構造，才驗得到記錄行為本身。
            return "GUARDIAN", False

    service = LooseService(
        family_tree_repository=FakeTreeRepository({OWNER: make_tree(OWNER, [])}),
        delegation_repository=FakeDelegationRepository(),
        enforcement_enabled=True,
    )
    # 該擁有者仍在影子狀態，因此最終仍依 legacy 拒絕（403）——但差異必須在
    # 拒絕之前就記下來，否則放寬方向的 bug 只會被 403 蓋掉、完全不留痕跡。
    with caplog.at_level(logging.INFO):
        with pytest.raises(HTTPException):
            await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ")
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "放寬方向的差異必須以較高層級記錄"
    assert "loosen" in str(errors[0].args)


# ── 欄位遮蔽（fail-closed）──────────────────────────────────────────


MEDICATION_PAYLOAD = {
    "id": "m1",
    "user_id": OWNER,
    "name": "Metformin",
    "indication": "糖尿病",
    "spc_indication": "第二型糖尿病",
    "spc_indication_summary": "控制血糖",
    "brand_new_field": "尚未登記",
}


@pytest.mark.asyncio
async def test_member_gets_medication_without_indication():
    service = make_service({})
    masked = service.mask(MEDICATION_PAYLOAD, "medication", "MEMBER")
    assert masked["name"] == "Metformin"
    assert "indication" not in masked
    assert "spc_indication" not in masked
    assert "spc_indication_summary" not in masked


@pytest.mark.asyncio
async def test_caregiver_sees_indication():
    service = make_service({})
    masked = service.mask(MEDICATION_PAYLOAD, "medication", "CAREGIVER")
    assert masked["indication"] == "糖尿病"


@pytest.mark.asyncio
async def test_unregistered_field_is_never_exposed_cross_user():
    """fail-closed：未登記的欄位對任何角色都不輸出，包含最高權限者。"""
    service = make_service({})
    for role in ("GUARDIAN", "CAREGIVER", "MEMBER"):
        masked = service.mask(MEDICATION_PAYLOAD, "medication", role)
        assert "brand_new_field" not in masked


@pytest.mark.asyncio
async def test_self_access_is_not_masked():
    """讀自己的資料不經遮蔽，否則新增欄位會連本人都看不到自己的資料。"""
    service = make_service({})
    masked = service.mask(MEDICATION_PAYLOAD, "medication", "OWNER", is_self=True)
    assert masked == MEDICATION_PAYLOAD
    assert masked["brand_new_field"] == "尚未登記"


@pytest.mark.asyncio
async def test_mask_response_returns_payload_untouched_for_self():
    service = make_service({})
    result = await service.mask_response(
        MEDICATION_PAYLOAD, "medication", OWNER, OWNER
    )
    assert result == MEDICATION_PAYLOAD


@pytest.mark.asyncio
async def test_mask_response_does_not_mask_in_shadow_mode():
    """遮蔽也是一種收緊，影子模式下不得生效。

    導入前沒有任何遮蔽。若在影子狀態就把適應症拿掉，使用者會在沒有任何切換
    的情況下發現東西不見了——那正是影子模式要避免的事。
    """
    service = service_with_role("MEMBER", state="shadow")
    result = await service.mask_response(
        MEDICATION_PAYLOAD, "medication", OPERATOR, OWNER
    )
    assert result == MEDICATION_PAYLOAD
    assert result["indication"] == "糖尿病"


@pytest.mark.asyncio
async def test_mask_response_masks_once_enforced():
    service = service_with_role("MEMBER", state="enforced")
    result = await service.mask_response(
        MEDICATION_PAYLOAD, "medication", OPERATOR, OWNER
    )
    assert "indication" not in result
    assert result["name"] == "Metformin"


@pytest.mark.asyncio
async def test_deliberately_unexposed_profile_fields_are_masked():
    """`role`／`settings` 刻意不登記——家人沒有理由知道你是不是管理員。"""
    service = make_service({})
    profile = {
        "line_id": OWNER,
        "name": "王大明",
        "picture_url": "https://example.invalid/a.png",
        "age": 82,
        "role": "admin",
        "settings": {"font_size": "xlarge"},
    }
    masked = service.mask(profile, "health_profile", "GUARDIAN")
    assert masked["name"] == "王大明"
    assert masked["age"] == 82
    assert "role" not in masked
    assert "settings" not in masked


@pytest.mark.asyncio
async def test_member_gets_identity_only_profile():
    service = make_service({})
    profile = {"line_id": OWNER, "name": "王大明", "age": 82, "chronic_diseases": ["dm"]}
    masked = service.mask(profile, "health_profile", "MEMBER")
    assert masked == {"line_id": OWNER, "name": "王大明"}


@pytest.mark.asyncio
async def test_nested_medications_are_masked_inside_reminder():
    """巢狀資源要遞迴遮蔽。

    少了這一步，外層看到 `medications` 自己登記為 GENERAL 就整包放行，
    適應症會從巢狀結構裡漏出去，而外層看起來一切正常。
    """
    service = make_service({})
    reminder = {
        "id": "r1",
        "user_id": OWNER,
        "slot_type": "morning",
        "medications": [MEDICATION_PAYLOAD],
    }
    masked = service.mask(reminder, "medication_reminder", "MEMBER")
    assert masked["medications"][0]["name"] == "Metformin"
    assert "indication" not in masked["medications"][0]
    assert "brand_new_field" not in masked["medications"][0]


@pytest.mark.asyncio
async def test_mask_accepts_a_list_payload():
    service = make_service({})
    masked = service.mask([MEDICATION_PAYLOAD], "medication", "MEMBER")
    assert isinstance(masked, list)
    assert "indication" not in masked[0]


@pytest.mark.asyncio
async def test_stranger_sees_nothing_at_all():
    """角色為 None 時所有分類都不可讀，遮蔽後應該是空的。"""
    service = make_service({})
    assert service.mask(MEDICATION_PAYLOAD, "medication", None) == {}


# ── 通知政策 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_member_is_not_notified():
    service = service_with_role("MEMBER")
    assert (
        await service.can_notify(OPERATOR, OWNER, "high_risk_drug_alert") is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER"])
async def test_guardian_and_caregiver_are_notified(role):
    service = service_with_role(role)
    assert await service.can_notify(OPERATOR, OWNER, "high_risk_drug_alert") is True


@pytest.mark.asyncio
async def test_subject_is_always_notified():
    service = make_service({})
    assert await service.can_notify(OWNER, OWNER, "high_risk_drug_alert") is True


def _mixed_family(state: str):
    return {
        OWNER: make_tree(
            OWNER,
            [
                FamilyMember(user_id="U-g", family_role="GUARDIAN"),
                FamilyMember(user_id="U-c", family_role="CAREGIVER"),
                FamilyMember(user_id="U-m", family_role="MEMBER"),
                FamilyMember(user_id="U-unset"),
            ],
            state=state,
        )
    }


@pytest.mark.asyncio
async def test_notification_recipients_are_not_the_whole_family():
    """收件人是判定的結果，不是「族譜全部成員」這條規則。"""
    service = make_service(_mixed_family("enforced"))
    recipients = await service.notification_recipients(OWNER, "high_risk_drug_alert")
    assert set(recipients) == {"U-g", "U-c"}


@pytest.mark.asyncio
async def test_notification_recipients_keep_whole_family_in_shadow():
    """收斂收件人也是一種收緊，影子模式下不得生效。

    通報是使用者最不該「安靜地少收到」的一種訊息：導入前族譜全員都收得到，
    在沒有任何切換的情況下突然少收到，比看不到某個欄位嚴重得多。
    """
    service = make_service(_mixed_family("shadow"))
    recipients = await service.notification_recipients(OWNER, "high_risk_drug_alert")
    assert set(recipients) == {"U-g", "U-c", "U-m", "U-unset"}


@pytest.mark.asyncio
async def test_no_qualified_recipients_returns_empty_not_everyone():
    service = make_service(
        {
            OWNER: make_tree(
                OWNER,
                [FamilyMember(user_id="U-m", family_role="MEMBER")],
                state="enforced",
            )
        }
    )
    assert await service.notification_recipients(OWNER, "high_risk_drug_alert") == []


@pytest.mark.asyncio
async def test_notification_does_not_grant_any_read_permission():
    """收到通知 SHALL NOT 改變收件人的任何資料存取權。"""
    service = service_with_role("CAREGIVER")
    assert await service.can_notify(OPERATOR, OWNER, "high_risk_drug_alert") is True
    assert await service.can(OPERATOR, OWNER, "PRIVATE", "READ") is False
    assert await service.can(OPERATOR, OWNER, "SENSITIVE", "WRITE") is False


# ── 引導式角色指派的完成判定 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_assignment_incomplete_when_a_member_has_no_role():
    service = make_service(
        {
            OWNER: make_tree(
                OWNER,
                [
                    FamilyMember(user_id="U-a", family_role="GUARDIAN"),
                    FamilyMember(user_id="U-b"),
                ],
            )
        }
    )
    status = await service.role_assignment_status(OWNER)
    assert status.is_complete is False
    assert status.unassigned_member_ids == ["U-b"]


@pytest.mark.asyncio
async def test_explicit_member_counts_as_assigned():
    """明確設定為 MEMBER 算已設定；缺欄位才算未設定。"""
    service = make_service(
        {
            OWNER: make_tree(
                OWNER, [FamilyMember(user_id="U-a", family_role="MEMBER")]
            )
        }
    )
    status = await service.role_assignment_status(OWNER)
    assert status.is_complete is True
    assert status.unassigned_member_ids == []


@pytest.mark.asyncio
async def test_empty_family_counts_as_complete():
    """沒有人要指派，不該把擁有者卡在引導畫面。"""
    service = make_service({OWNER: make_tree(OWNER, [])})
    assert (await service.role_assignment_status(OWNER)).is_complete is True


# ── enforcement 不因新增成員而回退 ──────────────────────────────────


@pytest.mark.asyncio
async def test_adding_a_member_does_not_revert_enforcement():
    """「新增家庭成員 → 觸發 legacy fallback」是一條提權路徑，必須封死。

    若強制會隨新成員回退，任何能建立邀請的人只要拉一個新帳號進來，整個家庭
    就退回變更前的寬鬆行為，本能力的所有約束一次全部失效。加入成員是低成本、
    可重複、看起來完全無害的動作。
    """
    tree = make_tree(
        OWNER,
        [FamilyMember(user_id=OPERATOR, family_role="MEMBER")],
        state="enforced",
    )
    service = make_service({OWNER: tree})

    # 新成員加入，且尚未指派角色 → 完成狀態回到未完成
    tree.family_members.append(FamilyMember(user_id="U-newcomer"))
    status = await service.role_assignment_status(OWNER)
    assert status.is_complete is False

    # 但遷移狀態不變，強制照舊生效
    assert await service.migration_state(OWNER) == "enforced"
    with pytest.raises(HTTPException):
        await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ")


@pytest.mark.asyncio
async def test_newcomer_without_role_is_treated_as_member():
    tree = make_tree(OWNER, [FamilyMember(user_id="U-newcomer")], state="enforced")
    service = make_service({OWNER: tree})
    assert await service.resolve_role("U-newcomer", OWNER) == "MEMBER"
    assert await service.can("U-newcomer", OWNER, "GENERAL", "READ") is True
    assert await service.can("U-newcomer", OWNER, "SENSITIVE", "READ") is False


@pytest.mark.asyncio
async def test_existing_members_keep_their_control_after_a_newcomer_joins():
    """新成員加入 SHALL NOT 降低既有成員的權限控制。"""
    tree = make_tree(
        OWNER,
        [FamilyMember(user_id=OPERATOR, family_role="GUARDIAN")],
        state="enforced",
    )
    service = make_service({OWNER: tree})
    before = await service.resolve_role(OPERATOR, OWNER)
    tree.family_members.append(FamilyMember(user_id="U-newcomer"))
    assert await service.resolve_role(OPERATOR, OWNER) == before == "GUARDIAN"


# ── describe（呈現面）────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_describe_reflects_enforced_matrix():
    service = service_with_role("MEMBER", state="enforced")
    described = await service.describe(OPERATOR, [OWNER])
    assert described[OWNER]["general"] == ["READ"]
    assert described[OWNER]["sensitive"] == []
    assert described[OWNER]["private"] == []


@pytest.mark.asyncio
async def test_describe_reflects_legacy_behaviour_in_shadow():
    """影子模式下描述的是「現在真的能做什麼」，不是矩陣的理論值。"""
    service = service_with_role("MEMBER", state="shadow")
    described = await service.describe(OPERATOR, [OWNER])
    assert described[OWNER]["sensitive"] == ["READ"]
    assert described[OWNER]["private"] == ["READ"]
    # 舊碼從來沒有代寫健康資料的路徑，不能描述成有
    assert "WRITE" not in described[OWNER]["sensitive"]


@pytest.mark.asyncio
async def test_describe_gives_nothing_to_a_stranger():
    service = service_with_role(None, state="shadow")
    described = await service.describe(STRANGER, [OWNER])
    assert described[OWNER] == {"general": [], "sensitive": [], "private": []}


# ── 遷移指標的計數（判準 1／4 的原始資料）──────────────────────────


class RecordingMetrics:
    def __init__(self, explode: bool = False):
        self.decisions = []
        self.diffs = []
        self._explode = explode

    async def record_decision(self, owner_id):
        if self._explode:
            raise RuntimeError("Mongo 掛了")
        self.decisions.append(owner_id)

    async def record(self, owner_id, direction):
        if self._explode:
            raise RuntimeError("Mongo 掛了")
        self.diffs.append((owner_id, direction))


def service_with_metrics(role, state="shadow", metrics=None):
    members = [] if role is None else [FamilyMember(user_id=OPERATOR, family_role=role)]
    return FamilyAuthorizationService(
        family_tree_repository=FakeTreeRepository(
            {OWNER: make_tree(OWNER, members, state=state)}
        ),
        delegation_repository=FakeDelegationRepository(),
        enforcement_enabled=True,
        metrics_repository=metrics,
    )


@pytest.mark.asyncio
async def test_tighten_diff_is_counted():
    """判準 1 的分子。"""
    metrics = RecordingMetrics()
    service = service_with_metrics("MEMBER", metrics=metrics)

    await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ")

    assert metrics.diffs == [(OWNER, "tighten")]


@pytest.mark.asyncio
async def test_every_decision_is_counted_as_the_denominator():
    """只有分子沒有分母算不出比例。一致的判定也要計入。"""
    metrics = RecordingMetrics()
    service = service_with_metrics("GUARDIAN", metrics=metrics)

    await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ")

    assert metrics.decisions == [OWNER]
    assert metrics.diffs == []


@pytest.mark.asyncio
async def test_counters_are_keyed_by_the_target_owner():
    """判準要問的是「這位擁有者能不能進入強制」，是逐家庭的問題。"""
    metrics = RecordingMetrics()
    service = service_with_metrics("MEMBER", metrics=metrics)

    await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ")

    assert all(owner_id == OWNER for owner_id in metrics.decisions)
    assert all(owner_id == OWNER for owner_id, _ in metrics.diffs)


@pytest.mark.asyncio
async def test_metrics_failure_never_breaks_authorization():
    """指標是觀測工具，不是安全邊界。

    一個計數器寫不進去就擋掉使用者的請求，是把觀測工具變成單點故障。
    """
    service = service_with_metrics("GUARDIAN", metrics=RecordingMetrics(explode=True))

    assert await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ") == "GUARDIAN"


@pytest.mark.asyncio
async def test_no_metrics_repository_means_no_behaviour_change():
    """未注入計數器時，授權行為與注入前完全相同。"""
    service = service_with_metrics("MEMBER", metrics=None)

    assert await service.authorize(OPERATOR, OWNER, "SENSITIVE", "READ") == "MEMBER"
