"""每一支跨使用者端點的授權行為，以 HTTP 直接驗證。

這裡回答的是「授權真的接上了嗎」——不是「服務層的判定對不對」（那在
tests/unit/services/family/ 已經窮舉過），而是「打這支 URL 會發生什麼事」。
所有請求都繞過前端，因為前端從來不是安全邊界。

每支端點至少涵蓋：OWNER（自己）／GUARDIAN／CAREGIVER／MEMBER／非家庭成員，
以及 shadow 與 enforced 兩種遷移狀態。

授權服務是**真的** `FamilyAuthorizationService`，只把 repository 換成假的——
整包 mock 掉的話，403 那條路根本不會執行，測試就變成只在測 mock 自己。
"""

from datetime import datetime, timezone
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_family_authorization_service,
    get_medication_service,
    get_user_profile_service,
    get_consultation_service,
)
from app.main import app
from app.models.family_tree import FamilyMember, FamilyTree
from app.models.medication import Medication, MedicationReminderWithMedications
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
ME = "U_ME"
ELDER = "U_ELDER"
STRANGER = "U_STRANGER"


class _Trees:
    def __init__(self, trees):
        self.trees = trees
        self.reads = []

    async def get_by_user_id(self, user_id):
        self.reads.append(user_id)
        return self.trees.get(user_id)


class _NoDelegations:
    async def has_active_delegation(self, owner_id, delegate_user_id, now=None):
        return False


def build_authz(role: Optional[str], state: str = "enforced", enforcement=True):
    """建一個「ME 對 ELDER 是 role」的授權服務。role=None 代表不是家人。"""
    members = [] if role is None else [FamilyMember(user_id=ME, family_role=role)]
    trees = {
        ELDER: FamilyTree(
            user_id=ELDER,
            family_members=members,
            rbac_migration_state=state,
            created_at=NOW,
            updated_at=NOW,
        )
    }
    return FamilyAuthorizationService(
        family_tree_repository=_Trees(trees),
        delegation_repository=_NoDelegations(),
        enforcement_enabled=enforcement,
    )


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    app.dependency_overrides.clear()


def wire(role, state="enforced", caller=ME, enforcement=True):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id=caller
    )
    app.dependency_overrides[get_family_authorization_service] = lambda: build_authz(
        role, state, enforcement
    )


ELDER_PROFILE = {
    "line_id": ELDER,
    "name": "王大明",
    "picture_url": "https://example.invalid/a.png",
    "age": 82,
    "gender": "male",
    "height": 165.0,
    "weight": 60.0,
    "chronic_diseases": ["hypertension"],
    "chronic_custom": [],
    "major_illness_history": "",
    "surgery_history": "",
    "role": "user",
    "settings": {"font_size": "xlarge"},
}


def wire_profile_service():
    """注入**真的** `UserProfileService`，只把 repository 換成假的。

    這裡刻意不用假的 service：先前那樣寫時，代理寫入的整條服務層路徑都沒被
    執行過，於是「payload 少了 name 就 ValidationError」這個 500 一路躲過了
    所有測試，直到人工 E2E 才炸出來。假掉的那一層，正好就是出問題的那一層。
    """
    from app.services.users.user_profile_service import UserProfileService

    class _Repo:
        async def get_user_profile(self, user_id):
            return dict(ELDER_PROFILE)

        async def upsert_user_profile(self, user_id, payload):
            self.written = payload
            return True

    repo = _Repo()
    service = UserProfileService(repo)
    service.repo = repo  # 測試要斷言實際寫進資料層的是什麼
    app.dependency_overrides[get_user_profile_service] = lambda: service
    return service


# ── GET /api/profiles/{userId} ───────────────────────────────────────


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER"])
def test_profile_sensitive_readers_get_health_fields(client, role):
    wire(role)
    wire_profile_service()
    res = client.get(f"/api/profiles/{ELDER}")
    assert res.status_code == 200
    assert res.json()["age"] == 82
    assert res.json()["chronic_diseases"] == ["hypertension"]


def test_profile_member_gets_200_with_identity_only(client):
    """MEMBER 拿到的是 200 + 遮蔽，**不是 403**。

    回 403 會讓前端誤以為連這個人是誰都不能知道，但族譜清單上明明就顯示著
    他的名字——那會逼前端把「沒有權限」與「載入失敗」混為一談。
    """
    wire("MEMBER")
    wire_profile_service()
    res = client.get(f"/api/profiles/{ELDER}")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "王大明"
    assert body["picture_url"]
    assert "age" not in body
    assert "chronic_diseases" not in body


def test_profile_masks_deliberately_unexposed_fields(client):
    """`role`／`settings` 刻意不登記——家人沒有理由知道你是不是管理員。"""
    wire("GUARDIAN")
    wire_profile_service()
    body = client.get(f"/api/profiles/{ELDER}").json()
    assert "role" not in body
    assert "settings" not in body


def test_profile_stranger_gets_403(client):
    wire(None)
    profiles = wire_profile_service()
    res = client.get(f"/api/profiles/{ELDER}")
    assert res.status_code == 403
    assert not hasattr(profiles.repo, "written")


def test_profile_owner_reads_own_unmasked(client):
    """查自己不遮蔽，也不解析角色。"""
    wire(None, caller=ELDER)
    wire_profile_service()
    body = client.get(f"/api/profiles/{ELDER}").json()
    assert body["age"] == 82
    assert body["role"] == "user"


def test_profile_shadow_mode_is_not_masked(client):
    """影子模式行為與導入前完全相同——包括不遮蔽。"""
    wire("MEMBER", state="shadow")
    wire_profile_service()
    body = client.get(f"/api/profiles/{ELDER}").json()
    assert body["age"] == 82


# ── PUT /api/profiles/{userId}（新增的代理寫入）────────────────────


PROFILE_PAYLOAD = {
    "name": "被改掉的名字",
    "gender": "male",
    "height": 166.0,
    "weight": 61.0,
    "age": 83,
    "chronic_diseases": ["hypertension"],
    "chronic_custom": [],
    "major_illness_history": "",
    "surgery_history": "",
    "health_consultations": {},
}


def test_proxy_write_allowed_for_guardian(client):
    wire("GUARDIAN")
    profiles = wire_profile_service()
    res = client.put(f"/api/profiles/{ELDER}", json=PROFILE_PAYLOAD)
    assert res.status_code == 200
    assert profiles.repo.written["age"] == 83


def test_proxy_write_rejected_for_caregiver(client):
    """CAREGIVER 對 SENSITIVE 只有讀取權。"""
    wire("CAREGIVER")
    profiles = wire_profile_service()
    res = client.put(f"/api/profiles/{ELDER}", json=PROFILE_PAYLOAD)
    assert res.status_code == 403
    assert not hasattr(profiles.repo, "written")


def test_proxy_write_rejected_for_member_even_in_shadow_mode(client):
    """新增的能力不受影子模式放寬。

    這條路徑在本能力導入前根本不存在，「與導入前相同」的意思是**沒有這個
    能力**。若沿用 legacy，一位 MEMBER 會在遷移期間取得他在強制之後反而
    沒有的寫入權。
    """
    wire("MEMBER", state="shadow")
    profiles = wire_profile_service()
    res = client.put(f"/api/profiles/{ELDER}", json=PROFILE_PAYLOAD)
    assert res.status_code == 403
    assert not hasattr(profiles.repo, "written")


def test_proxy_write_never_touches_display_identity(client):
    """分類回答「誰看得到」，不回答「誰改得動」。"""
    wire("GUARDIAN")
    profiles = wire_profile_service()
    res = client.put(f"/api/profiles/{ELDER}", json=PROFILE_PAYLOAD)
    assert res.status_code == 200
    written = profiles.repo.written
    assert "name" not in written
    assert "name" in res.json()["skipped_fields"]
    # 頭像與介面偏好也不得被碰。`upsert_user_profile` 會把它們補成預設值再
    # 寫回去，那會清掉被照顧者的頭像——代理寫入必須走部分更新。
    assert "picture_url" not in written
    assert "settings" not in written
    assert "role" not in written


def test_proxy_write_accepts_a_body_without_name(client):
    """`name` 不歸這條路徑管，因此 SHALL NOT 成為必填。

    這是實際打回來的缺陷：body 型別沿用 `UserProfileData`（`name` 必填且
    min_length=1），但處理函式隨即把 `name` 剝除。於是 endpoint 要求一個它
    丟棄的欄位，呼叫端唯一的過關方式是送一個假值——代填介面刻意不提供姓名
    輸入，送出時就吃 422。

    上面那些測試全都送完整 body（含非空的 name），所以這個矛盾一直沒現形。
    """
    wire("GUARDIAN")
    profiles = wire_profile_service()
    body = {k: v for k, v in PROFILE_PAYLOAD.items() if k != "name"}
    res = client.put(f"/api/profiles/{ELDER}", json=body)
    assert res.status_code == 200
    assert profiles.repo.written["age"] == 83
    assert res.json()["skipped_fields"] == []


def test_proxy_write_accepts_an_empty_name_and_still_discards_it(client):
    """前端把讀回來的舊值原樣送回時，那個值可能是空字串。

    空字串在這裡不是「請把名字清空」——`name` 根本不可寫。它應該和其他不可
    寫欄位一樣被剝除並回報，而不是變成驗證錯誤。
    """
    wire("GUARDIAN")
    profiles = wire_profile_service()
    res = client.put(f"/api/profiles/{ELDER}", json={**PROFILE_PAYLOAD, "name": ""})
    assert res.status_code == 200
    assert "name" not in profiles.repo.written
    assert "name" in res.json()["skipped_fields"]


def test_proxy_write_only_writes_the_fields_actually_sent(client):
    """部分更新 SHALL NOT 把沒帶到的欄位寫成 null。

    欄位改為全部可選之後，若仍以 `model_dump()`（不帶 exclude_unset）取值，
    沒送的鍵會以 None 進 `$set`——呼叫端只想補一個年齡，卻會清掉被照顧者的
    身高、慢性病與病史。這條測試釘住的是那個方向。
    """
    wire("GUARDIAN")
    profiles = wire_profile_service()
    res = client.put(f"/api/profiles/{ELDER}", json={"age": 84})
    assert res.status_code == 200
    written = profiles.repo.written
    assert written["age"] == 84
    for untouched in (
        "height",
        "weight",
        "gender",
        "chronic_diseases",
        "chronic_custom",
        "major_illness_history",
        "surgery_history",
    ):
        assert untouched not in written


def test_proxy_write_still_validates_the_values_it_does_accept(client):
    """可選不等於不驗。帶到的值仍要合乎範圍。"""
    wire("GUARDIAN")
    profiles = wire_profile_service()
    assert client.put(f"/api/profiles/{ELDER}", json={"age": 999}).status_code == 422
    assert client.put(f"/api/profiles/{ELDER}", json={"height": 0}).status_code == 422
    assert not hasattr(profiles.repo, "written")


def test_proxy_write_rejects_stranger(client):
    wire(None)
    profiles = wire_profile_service()
    assert client.put(f"/api/profiles/{ELDER}", json=PROFILE_PAYLOAD).status_code == 403
    assert not hasattr(profiles.repo, "written")


# ── GET /api/consultations/{userId}/* ────────────────────────────────


class _Consultations:
    def __init__(self):
        self.summary_calls = []
        self.raw_calls = []

    async def get_all_summaries(self, user_id):
        self.summary_calls.append(user_id)
        return []

    async def get_raw_view(self, user_id):
        self.raw_calls.append(user_id)
        return []


def wire_consultations():
    service = _Consultations()
    app.dependency_overrides[get_consultation_service] = lambda: service
    return service


@pytest.mark.parametrize("role", ["CAREGIVER", "MEMBER"])
@pytest.mark.parametrize(
    "path", ["allsummaries", "messages/raw"]
)
def test_consultations_denied_without_private_read(client, role, path):
    """PRIVATE 只有 OWNER 與 GUARDIAN 讀得到。"""
    wire(role)
    consultations = wire_consultations()
    res = client.get(f"/api/consultations/{ELDER}/{path}")
    assert res.status_code == 403
    # 授權擋在讀取之前：最敏感的資料不該有「先撈出來再說」的路徑
    assert consultations.summary_calls == []
    assert consultations.raw_calls == []


def test_consultations_allowed_for_guardian(client):
    wire("GUARDIAN")
    consultations = wire_consultations()
    assert client.get(f"/api/consultations/{ELDER}/allsummaries").status_code == 200
    assert consultations.summary_calls == [ELDER]


def test_consultations_denied_for_stranger(client):
    wire(None)
    consultations = wire_consultations()
    assert client.get(f"/api/consultations/{ELDER}/allsummaries").status_code == 403
    assert consultations.summary_calls == []


def test_consultations_allowed_for_member_in_shadow_mode(client):
    """影子模式保留既有能力：變更前族譜成員本來就讀得到。"""
    wire("MEMBER", state="shadow")
    consultations = wire_consultations()
    assert client.get(f"/api/consultations/{ELDER}/allsummaries").status_code == 200
    assert consultations.summary_calls == [ELDER]


# ── GET /api/medications/reminders ──────────────────────────────────


def _reminder_with_indication():
    medication = Medication(
        _id="m1",
        user_id=ELDER,
        created_by_user_id=ELDER,
        name="Metformin",
        indication="糖尿病",
        spc_indication="第二型糖尿病",
        spc_indication_summary="控制血糖",
    )
    return MedicationReminderWithMedications(
        _id="r1",
        creator_user_id=ME,
        user_id=ELDER,
        slot_type="morning",
        scheduled_time="08:00",
        medication_ids=["m1"],
        medications=[medication],
    )


class _Medications:
    def __init__(self):
        self.calls = []

    async def get_user_reminders_with_medications(self, user_id, requester_user_id=None):
        self.calls.append(user_id)
        return [_reminder_with_indication()]


def wire_medications():
    service = _Medications()
    app.dependency_overrides[get_medication_service] = lambda: service
    return service


def test_reminders_member_gets_200_without_indication(client):
    """混合分類端點：用藥是 GENERAL，適應症是 SENSITIVE。

    MEMBER 拿到 200 與完整的藥品、時段，適應症為空——**不是 403**。回 403
    會讓他連「長輩早上要吃三種藥」都不知道，而那本來就是他有權知道的。
    """
    wire("MEMBER")
    wire_medications()
    res = client.get(f"/api/medications/reminders?target_user_id={ELDER}")
    assert res.status_code == 200
    body = res.json()[0]
    assert body["slot_type"] == "morning"
    medication = body["medications"][0]
    assert medication["name"] == "Metformin"
    assert medication["indication"] is None
    assert medication["spc_indication"] is None
    assert medication["spc_indication_summary"] is None


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER"])
def test_reminders_sensitive_readers_see_indication(client, role):
    wire(role)
    wire_medications()
    body = client.get(f"/api/medications/reminders?target_user_id={ELDER}").json()
    assert body[0]["medications"][0]["indication"] == "糖尿病"


def test_reminders_stranger_gets_403(client):
    wire(None)
    medications = wire_medications()
    res = client.get(f"/api/medications/reminders?target_user_id={ELDER}")
    assert res.status_code == 403
    assert medications.calls == []


def test_reminders_shadow_mode_keeps_indication_visible(client):
    """遮蔽也是一種收緊，影子模式下不得生效。"""
    wire("MEMBER", state="shadow")
    wire_medications()
    body = client.get(f"/api/medications/reminders?target_user_id={ELDER}").json()
    assert body[0]["medications"][0]["indication"] == "糖尿病"


def test_reminders_self_is_not_masked(client):
    wire(None, caller=ELDER)
    wire_medications()
    body = client.get("/api/medications/reminders").json()
    assert body[0]["medications"][0]["indication"] == "糖尿病"


# ── POST/PUT/DELETE /api/medications/reminders ──────────────────────


CREATE_PAYLOAD = {"user_id": ELDER, "slots": ["morning"]}


class _WritableMedications(_Medications):
    def __init__(self):
        super().__init__()
        self.created = []
        self.updated = []
        self.deleted = []

    async def create_reminders(self, creator_user_id, request):
        self.created.append(request.user_id)
        return []

    async def get_reminder(self, reminder_id):
        return _reminder_with_indication()

    async def update_reminder(self, creator_user_id, reminder_id, request):
        self.updated.append(reminder_id)
        return _reminder_with_indication()

    async def delete_reminder(self, creator_user_id, reminder_id):
        self.deleted.append(reminder_id)
        return True


def wire_writable_medications():
    service = _WritableMedications()
    app.dependency_overrides[get_medication_service] = lambda: service
    return service


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER"])
def test_create_reminder_allowed_for_general_writers(client, role):
    wire(role)
    service = wire_writable_medications()
    assert client.post("/api/medications/reminders", json=CREATE_PAYLOAD).status_code == 200
    assert service.created == [ELDER]


def test_create_reminder_denied_for_member(client):
    wire("MEMBER")
    service = wire_writable_medications()
    assert client.post("/api/medications/reminders", json=CREATE_PAYLOAD).status_code == 403
    assert service.created == []


def test_create_reminder_denied_for_stranger(client):
    wire(None)
    service = wire_writable_medications()
    assert client.post("/api/medications/reminders", json=CREATE_PAYLOAD).status_code == 403
    assert service.created == []


def test_update_reminder_authorizes_against_the_patient_not_the_creator(client):
    """`creator_user_id` 不得成為繞過授權的後門。

    這筆提醒的建立者正是呼叫者（見 `_reminder_with_indication`），但他對
    用藥者只是 MEMBER——變更前這樣就能改，現在不行。
    """
    wire("MEMBER")
    service = wire_writable_medications()
    res = client.put("/api/medications/reminders/r1", json={"enabled": False})
    assert res.status_code == 403
    assert service.updated == []


def test_update_reminder_allowed_for_caregiver(client):
    wire("CAREGIVER")
    service = wire_writable_medications()
    assert client.put("/api/medications/reminders/r1", json={"enabled": False}).status_code == 200
    assert service.updated == ["r1"]


def test_delete_reminder_denied_for_member_who_created_it(client):
    wire("MEMBER")
    service = wire_writable_medications()
    assert client.delete("/api/medications/reminders/r1").status_code == 403
    assert service.deleted == []


def test_delete_reminder_allowed_for_guardian(client):
    wire("GUARDIAN")
    service = wire_writable_medications()
    assert client.delete("/api/medications/reminders/r1").status_code == 200
    assert service.deleted == ["r1"]


# ── GET /api/medications/reminders/created ──────────────────────────


class _CreatorMedications:
    async def get_creator_reminders(self, creator_user_id):
        return [_reminder_with_indication()]


def test_created_list_filters_out_what_you_may_no_longer_read(client):
    """授權從前門關掉，不能留這扇後門。

    這支端點的篩選條件是 `creator_user_id`，而建立者已不再構成授權依據。
    若不逐筆判定，被降級的人仍能從這裡看到他當初為長輩設的全部用藥。
    """
    wire(None)  # 已不在長輩的族譜裡
    app.dependency_overrides[get_medication_service] = lambda: _CreatorMedications()
    res = client.get("/api/medications/reminders/created")
    assert res.status_code == 200
    assert res.json() == []


def test_created_list_keeps_items_you_may_still_read(client):
    wire("MEMBER")
    app.dependency_overrides[get_medication_service] = lambda: _CreatorMedications()
    body = client.get("/api/medications/reminders/created").json()
    assert len(body) == 1
    assert body[0]["user_id"] == ELDER


# ── POST /api/medications/prescription-drafts/{id}/commit ───────────


class _ScanService:
    def __init__(self):
        self.committed = []

    async def commit(self, draft_id, user_id, payload):
        self.committed.append(payload.user_id)
        return {"created_medication_ids": [], "reminder_ids": []}


def wire_scan():
    from app.dependencies import get_prescription_scan_service

    service = _ScanService()
    app.dependency_overrides[get_prescription_scan_service] = lambda: service
    return service


COMMIT_PAYLOAD = {"user_id": ELDER, "drugs": []}


def test_scan_commit_denied_for_member(client):
    """提交會一次寫入多筆藥品與提醒，授權必須擋在寫入之前。

    寫到一半才發現無權，留下的是半套資料。
    """
    wire("MEMBER")
    scan = wire_scan()
    res = client.post(
        "/api/medications/prescription-drafts/D1/commit", json=COMMIT_PAYLOAD
    )
    assert res.status_code == 403
    assert scan.committed == []


def test_scan_commit_denied_for_stranger(client):
    wire(None)
    scan = wire_scan()
    res = client.post(
        "/api/medications/prescription-drafts/D1/commit", json=COMMIT_PAYLOAD
    )
    assert res.status_code == 403
    assert scan.committed == []


def test_scan_commit_denied_for_member_even_in_shadow(client):
    """影子模式保留的是既有能力，不是把寫入權放寬給沒有的人。

    變更前這裡的檢查是「在族譜裡即可」——影子模式維持那個行為，因此
    MEMBER 在 shadow 下仍會通過。這條測試釘住的是：**強制之後**一定擋下。
    """
    wire("MEMBER", state="enforced")
    scan = wire_scan()
    res = client.post(
        "/api/medications/prescription-drafts/D1/commit", json=COMMIT_PAYLOAD
    )
    assert res.status_code == 403
    assert scan.committed == []


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER"])
def test_scan_commit_allowed_for_general_writers(client, role):
    wire(role)
    scan = wire_scan()
    res = client.post(
        "/api/medications/prescription-drafts/D1/commit", json=COMMIT_PAYLOAD
    )
    assert res.status_code == 200
    assert scan.committed == [ELDER]


# ── GET /api/family/me 的權限描述 ───────────────────────────────────


def test_family_me_reports_both_directions_and_effective_permissions(client):
    """族譜回應同時給兩個方向的角色，且權限是**實際生效**的值。

    `family_role` 是「他對我的資料」的角色（我可以改）；`my_role` 是「我對
    他的資料」的角色（他決定）。方向很容易讀反，所以兩個都給。
    """
    from app.dependencies import get_family_role_service, get_family_tree_service
    from app.models.family_tree import FamilyRoleAssignmentStatus

    my_tree = FamilyTree(
        user_id=ME,
        family_members=[FamilyMember(user_id=ELDER, family_role="CAREGIVER")],
        created_at=NOW,
        updated_at=NOW,
    )

    class _FamilyService:
        async def get_family_tree(self, user_id):
            return my_tree

    class _Roles:
        async def assignment_status(self, operator_id, owner_id):
            return FamilyRoleAssignmentStatus(
                owner_id=owner_id,
                is_complete=False,
                unassigned_member_ids=["U_OTHER"],
                rbac_migration_state="shadow",
            )

    class _BatchTrees:
        async def get_roles_for_operator(self, operator_id, owner_ids):
            return {
                ELDER: {"family_role": "GUARDIAN", "rbac_migration_state": "enforced"}
            }

    class _BatchDelegations:
        async def list_delegated_owner_ids(self, delegate_user_id, owner_ids, now=None):
            return []

    authz = FamilyAuthorizationService(
        family_tree_repository=_BatchTrees(),
        delegation_repository=_BatchDelegations(),
        enforcement_enabled=True,
    )

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(line_user_id=ME)
    app.dependency_overrides[get_family_tree_service] = lambda: _FamilyService()
    app.dependency_overrides[get_family_authorization_service] = lambda: authz
    app.dependency_overrides[get_family_role_service] = lambda: _Roles()

    body = client.get("/api/family/me").json()
    member = body["family_tree"]["family_members"][0]

    # 他對我的資料是 CAREGIVER（我族譜裡存的）
    assert member["family_role"] == "CAREGIVER"
    # 我對他的資料是 GUARDIAN（他族譜裡存的）
    assert member["my_role"] == "GUARDIAN"
    assert member["rbac_migration_state"] == "enforced"
    assert member["my_permissions"]["sensitive"] == ["READ", "WRITE"]
    assert member["my_permissions"]["private"] == ["READ"]
    # 引導式指派狀態一併回，族譜頁不必多打一次
    assert body["role_assignment"]["is_complete"] is False
    assert body["role_assignment"]["unassigned_member_ids"] == ["U_OTHER"]


def test_family_me_gives_no_permissions_for_a_member_whose_tree_excludes_me(client):
    """不在對方的族譜裡就沒有任何權限——SHALL NOT 給預設角色。"""
    from app.dependencies import get_family_role_service, get_family_tree_service
    from app.models.family_tree import FamilyRoleAssignmentStatus

    my_tree = FamilyTree(
        user_id=ME,
        family_members=[FamilyMember(user_id=STRANGER)],
        created_at=NOW,
        updated_at=NOW,
    )

    class _FamilyService:
        async def get_family_tree(self, user_id):
            return my_tree

    class _Roles:
        async def assignment_status(self, operator_id, owner_id):
            return FamilyRoleAssignmentStatus(owner_id=owner_id, is_complete=True)

    class _BatchTrees:
        async def get_roles_for_operator(self, operator_id, owner_ids):
            return {}

    class _BatchDelegations:
        async def list_delegated_owner_ids(self, delegate_user_id, owner_ids, now=None):
            return []

    authz = FamilyAuthorizationService(
        family_tree_repository=_BatchTrees(),
        delegation_repository=_BatchDelegations(),
        enforcement_enabled=True,
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(line_user_id=ME)
    app.dependency_overrides[get_family_tree_service] = lambda: _FamilyService()
    app.dependency_overrides[get_family_authorization_service] = lambda: authz
    app.dependency_overrides[get_family_role_service] = lambda: _Roles()

    member = client.get("/api/family/me").json()["family_tree"]["family_members"][0]
    assert member["my_role"] is None
    assert member["my_permissions"] == {"general": [], "sensitive": [], "private": []}
