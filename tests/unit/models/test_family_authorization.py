"""家庭授權四張表的窮舉測試與守門測試。

這裡測的是**資料**，不是行為：`app/models/family_authorization.py` 的四張表
是整套授權的唯一真相，任何一格被改動都必須是有意識的決定，因此逐格斷言而
不是抽樣。矩陣只有 4×3×2 = 24 種組合，全部寫出來的成本遠低於漏掉一格的代價。

最後一組是守門測試：跨使用者輸出模型每新增一個欄位，若沒有登記分類、也沒有
列入刻意不輸出的名單，測試就會失敗。那是 fail-closed 唯一的早期警報——
少了它，漏登記的欄位只會安靜地從家人畫面上消失（或在方向寫反時安靜地外洩）。
"""

import pytest

from app.models.family_authorization import (
    ASSIGNABLE_FAMILY_ROLES,
    CLASSIFICATION_OF,
    DEFAULT_FAMILY_ROLE,
    DEFAULT_MIGRATION_STATE,
    DELIBERATELY_UNEXPOSED_FIELDS,
    FIELD_CLASSIFICATION,
    NOTIFICATION_POLICY,
    PERMISSIONS,
    PROXY_WRITE_FORBIDDEN_FIELDS,
    field_classification,
    is_allowed,
    notification_recipient_roles,
)
from app.models.medication import Medication, MedicationReminderWithMedications
from app.models.user import UserProfile

# spec「權限矩陣」那張表，逐格複寫一次。
# 這裡刻意不從 PERMISSIONS 推導期望值——那會變成拿表跟自己比。
EXPECTED_MATRIX = {
    ("OWNER", "GENERAL"): {"READ", "WRITE"},
    ("OWNER", "SENSITIVE"): {"READ", "WRITE"},
    ("OWNER", "PRIVATE"): {"READ", "WRITE"},
    ("GUARDIAN", "GENERAL"): {"READ", "WRITE"},
    ("GUARDIAN", "SENSITIVE"): {"READ", "WRITE"},
    ("GUARDIAN", "PRIVATE"): {"READ"},
    ("CAREGIVER", "GENERAL"): {"READ", "WRITE"},
    ("CAREGIVER", "SENSITIVE"): {"READ"},
    ("CAREGIVER", "PRIVATE"): set(),
    ("MEMBER", "GENERAL"): {"READ"},
    ("MEMBER", "SENSITIVE"): set(),
    ("MEMBER", "PRIVATE"): set(),
}

ROLES = ["OWNER", "GUARDIAN", "CAREGIVER", "MEMBER"]
CLASSIFICATIONS = ["GENERAL", "SENSITIVE", "PRIVATE"]
ACTIONS = ["READ", "WRITE"]

# 跨使用者輸出的模型與其資源名稱。守門測試靠這份對照表找出「模型有、登記表
# 沒有」的欄位。新增這類模型時要一併加進來。
CROSS_USER_MODELS = {
    "medication_reminder": MedicationReminderWithMedications,
    "medication": Medication,
    "health_profile": UserProfile,
}


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("classification", CLASSIFICATIONS)
def test_matrix_cell_matches_spec(role, classification):
    assert PERMISSIONS[role][classification] == EXPECTED_MATRIX[
        (role, classification)
    ], f"{role} 對 {classification} 的權限與 spec 不符"


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("classification", CLASSIFICATIONS)
@pytest.mark.parametrize("action", ACTIONS)
def test_is_allowed_matches_matrix(role, classification, action):
    expected = action in EXPECTED_MATRIX[(role, classification)]
    assert is_allowed(role, classification, action) is expected


def test_non_member_has_no_permission_at_all():
    """不在族譜內時角色解析為 None，任何組合都不得放行。"""
    for classification in CLASSIFICATIONS:
        for action in ACTIONS:
            assert is_allowed(None, classification, action) is False


def test_write_does_not_imply_read():
    """WRITE 不得蘊含 READ。

    正式矩陣沒有「可寫不可讀」的格子，所以這條規則驗不出來——除非餵一張
    合成的矩陣進去。以參數注入而非 monkey patch，是專案既有的測試慣例。
    """
    write_only = {
        "MEMBER": {
            "GENERAL": frozenset({"WRITE"}),
            "SENSITIVE": frozenset(),
            "PRIVATE": frozenset(),
        }
    }
    assert is_allowed("MEMBER", "GENERAL", "WRITE", permissions=write_only) is True
    assert is_allowed("MEMBER", "GENERAL", "READ", permissions=write_only) is False


def test_read_does_not_imply_write():
    """MEMBER 對 GENERAL 只有 READ，SHALL NOT 因此取得 WRITE。"""
    assert is_allowed("MEMBER", "GENERAL", "READ") is True
    assert is_allowed("MEMBER", "GENERAL", "WRITE") is False


def test_owner_is_not_assignable():
    """OWNER 是「這份資料是誰的」的事實，不是可授予的角色。"""
    assert "OWNER" not in ASSIGNABLE_FAMILY_ROLES
    assert ASSIGNABLE_FAMILY_ROLES == frozenset({"GUARDIAN", "CAREGIVER", "MEMBER"})


def test_default_role_is_member():
    assert DEFAULT_FAMILY_ROLE == "MEMBER"


def test_default_migration_state_is_shadow():
    """預設不強制：新擁有者不會在沒指派任何角色的情況下就把家人擋在門外。"""
    assert DEFAULT_MIGRATION_STATE == "shadow"


def test_consultation_summary_and_raw_are_both_private():
    """摘要是原始對話的濃縮，降一級等於讓同一份內容從側門走出去。"""
    assert CLASSIFICATION_OF["consultation_summary"] == "PRIVATE"
    assert CLASSIFICATION_OF["consultation_raw"] == "PRIVATE"


def test_indication_fields_are_sensitive():
    """三個適應症欄位回答的都是「這個人為什麼吃這個藥」，同屬 SENSITIVE。"""
    for field in ("indication", "spc_indication", "spc_indication_summary"):
        assert field_classification("medication", field) == "SENSITIVE"


def test_display_identity_fields_are_general():
    """族譜清單靠這兩個欄位回答「這是誰」，權限最低的成員也看得到。"""
    assert field_classification("health_profile", "name") == "GENERAL"
    assert field_classification("health_profile", "picture_url") == "GENERAL"


def test_health_fields_are_sensitive():
    for field in (
        "age",
        "gender",
        "height",
        "weight",
        "chronic_diseases",
        "chronic_custom",
        "major_illness_history",
        "surgery_history",
    ):
        assert field_classification("health_profile", field) == "SENSITIVE"


def test_unregistered_field_returns_none_not_general():
    """fail-closed 的方向：查不到就是查不到，不得代換成資源的預設分類。"""
    assert field_classification("medication", "some_future_field") is None
    assert field_classification("health_profile", "role") is None


def test_proxy_write_forbidden_fields_cover_display_identity():
    """分類回答「誰看得到」，不回答「誰改得動」。

    `name`／`picture_url` 是 GENERAL（讀取面），但代理寫入一律不得碰它們，
    否則一位 CAREGIVER 就能改掉長輩在所有家人畫面上的名字。
    """
    for field in ("name", "display_name", "picture_url", "role", "settings"):
        assert field in PROXY_WRITE_FORBIDDEN_FIELDS


def test_member_is_not_a_notification_recipient():
    """MEMBER 有 GENERAL 讀取權，但 SHALL NOT 因此收到任何推播。"""
    for kind, roles in NOTIFICATION_POLICY.items():
        assert "MEMBER" not in roles, f"{kind} 不該把 MEMBER 列為收件人"


def test_high_risk_alert_recipients_are_guardian_and_caregiver():
    assert notification_recipient_roles("high_risk_drug_alert") == frozenset(
        {"GUARDIAN", "CAREGIVER"}
    )


def test_notification_policy_is_not_derived_from_permissions():
    """通知政策與讀取權是兩套獨立的表。

    若通知政策是從 PERMISSIONS 推導的，高風險通報的收件人就會等於「對
    SENSITIVE 有讀取權的角色集合」。實際上兩者不同——CAREGIVER 收得到通報，
    但那不代表他多拿到任何讀取權；而 OWNER 有全部讀取權，卻是當事人本人、
    不經這張表。這條測試釘住「不同」這件事本身。
    """
    sensitive_readers = {
        role for role in ROLES if is_allowed(role, "SENSITIVE", "READ")
    }
    assert notification_recipient_roles("high_risk_drug_alert") != sensitive_readers


def test_unknown_notification_kind_raises_rather_than_falls_back():
    """查不到的推播種類 SHALL 直接爆，SHALL NOT 悄悄落回讀取權。"""
    with pytest.raises(KeyError):
        notification_recipient_roles("not_a_real_kind")


@pytest.mark.parametrize("resource,model", sorted(CROSS_USER_MODELS.items()))
def test_every_cross_user_output_field_is_classified(resource, model):
    """守門測試：模型新增欄位而未登記分類時，這條 SHALL 失敗。

    這是 fail-closed 唯一的早期警報。少了它，新欄位會安靜地從家人畫面上
    消失，而沒有人知道原因出在授權表。
    """
    unclassified = [
        name
        for name in model.model_fields
        if (resource, name) not in FIELD_CLASSIFICATION
        and (resource, name) not in DELIBERATELY_UNEXPOSED_FIELDS
    ]
    assert not unclassified, (
        f"{model.__name__} 的下列欄位既未登記分類、也未列入刻意不輸出名單："
        f"{unclassified}。請在 FIELD_CLASSIFICATION 或 "
        f"DELIBERATELY_UNEXPOSED_FIELDS 擇一登記。"
    )


def test_field_classification_has_no_entry_for_unknown_model_field():
    """反向守門：登記表裡不得有模型上已不存在的欄位。

    欄位改名時若只改模型、沒改登記表，舊的登記會留下來繼續生效，而新名稱
    變成未登記——一邊是死掉的規則，一邊是靜默的遮蔽。
    """
    stale = [
        (resource, name)
        for (resource, name) in FIELD_CLASSIFICATION
        if resource in CROSS_USER_MODELS
        and name not in CROSS_USER_MODELS[resource].model_fields
    ]
    assert not stale, f"登記表指向已不存在的模型欄位：{stale}"
