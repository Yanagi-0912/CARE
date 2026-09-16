"""``GET``／``PUT /api/health/alert-thresholds`` 的授權行為
（health-alerts spec「誰能設定與查看提醒範圍」）。

同 tests/unit/routers/test_endpoint_authorization.py 的方法論：授權服務是
**真的** `FamilyAuthorizationService`，只把 repository 換成假的——整包
mock 掉的話，403 那條路根本不會執行。服務層換成假的、記錄呼叫，用來確認
授權真的擋在資料存取之前。

覆蓋本人、GUARDIAN、CAREGIVER、MEMBER、非家庭成員，以及 shadow 與
enforced 兩種遷移狀態；新增的能力（`has_legacy_equivalent=False`）在影子
模式下 MEMBER／CAREGIVER 寫入／陌生人仍是 403。

Task 4、5、6 會在這支端點與這支測試檔繼續往下擴充其他章節的授權測試，
新增區塊請沿用下面「── 區塊名稱 ──」的分隔慣例，接在檔尾。
"""

from datetime import date, datetime, timezone
from typing import Optional

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_family_authorization_service,
    get_health_alert_threshold_service,
    get_health_measurement_service,
    get_menstrual_record_service,
    get_step_service,
)
from app.main import app
from app.models.family_tree import FamilyMember, FamilyTree
from app.models.health import (
    CreateBloodPressureRequest,
    HealthAlertThreshold,
    HealthMeasurement,
    MenstrualRecord,
    StepCount,
)
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

    async def get_by_user_id(self, user_id):
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


class _FakeThresholdService:
    """假的服務層，記錄呼叫；用來確認 403 擋在資料存取之前。"""

    def __init__(self, existing: Optional[HealthAlertThreshold] = None):
        self.existing = existing
        self.get_calls: list[str] = []
        self.update_calls: list[tuple[str, str]] = []

    async def get_view(self, user_id: str):
        self.get_calls.append(user_id)
        if self.existing is None:
            empty = {
                "user_id": user_id,
                "systolic_high": None,
                "systolic_low": None,
                "diastolic_high": None,
                "diastolic_low": None,
                "glucose_fasting_high": None,
                "glucose_nonfasting_high": None,
                "glucose_low": None,
                "updated_by": None,
                "updated_at": None,
            }
            return empty
        return self.existing.model_dump()

    async def update(self, target_user_id, request, updated_by):
        self.update_calls.append((target_user_id, updated_by))
        return HealthAlertThreshold(
            user_id=target_user_id,
            updated_by=updated_by,
            **request.model_dump(),
        )


def wire_thresholds(existing: Optional[HealthAlertThreshold] = None):
    service = _FakeThresholdService(existing)
    app.dependency_overrides[get_health_alert_threshold_service] = lambda: service
    return service


PUT_PAYLOAD = {"systolic_high": 140, "systolic_low": 100}


# ── GET /api/health/alert-thresholds ─────────────────────────────────


def test_get_self_always_allowed_without_any_family_relation(client):
    """本人查自己：不需要族譜裡有任何關係就能過。"""
    wire(None, caller=ELDER)
    service = wire_thresholds()
    res = client.get(f"/api/health/alert-thresholds?user_id={ELDER}")
    assert res.status_code == 200
    assert service.get_calls == [ELDER]


def test_get_self_via_omitted_user_id(client):
    """user_id 省略時為本人。"""
    wire(None, caller=ELDER)
    service = wire_thresholds()
    res = client.get("/api/health/alert-thresholds")
    assert res.status_code == 200
    assert service.get_calls == [ELDER]


def test_get_returns_all_null_when_no_document_exists(client):
    """從未設定過時：200，不是 404，全部欄位為 null。"""
    wire("GUARDIAN")
    wire_thresholds(existing=None)
    res = client.get(f"/api/health/alert-thresholds?user_id={ELDER}")
    assert res.status_code == 200
    body = res.json()
    assert body["user_id"] == ELDER
    for field in (
        "systolic_high",
        "systolic_low",
        "diastolic_high",
        "diastolic_low",
        "glucose_fasting_high",
        "glucose_nonfasting_high",
        "glucose_low",
        "updated_by",
        "updated_at",
    ):
        assert body[field] is None


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER"])
def test_get_allowed_for_sensitive_readers(client, role):
    wire(role)
    wire_thresholds(
        existing=HealthAlertThreshold(
            user_id=ELDER, systolic_high=140, updated_by=ELDER
        )
    )
    res = client.get(f"/api/health/alert-thresholds?user_id={ELDER}")
    assert res.status_code == 200
    assert res.json()["systolic_high"] == 140


def test_get_denied_for_member(client):
    wire("MEMBER")
    service = wire_thresholds()
    res = client.get(f"/api/health/alert-thresholds?user_id={ELDER}")
    assert res.status_code == 403
    assert service.get_calls == []


def test_get_denied_for_stranger(client):
    wire(None)
    service = wire_thresholds()
    res = client.get(f"/api/health/alert-thresholds?user_id={ELDER}")
    assert res.status_code == 403
    assert service.get_calls == []


def test_get_denied_for_member_even_in_shadow_mode(client):
    """新增的能力不受影子模式放寬（constraints.md「Authorization」）。"""
    wire("MEMBER", state="shadow")
    service = wire_thresholds()
    res = client.get(f"/api/health/alert-thresholds?user_id={ELDER}")
    assert res.status_code == 403
    assert service.get_calls == []


def test_get_denied_for_stranger_even_in_shadow_mode(client):
    wire(None, state="shadow")
    service = wire_thresholds()
    res = client.get(f"/api/health/alert-thresholds?user_id={ELDER}")
    assert res.status_code == 403
    assert service.get_calls == []


# ── PUT /api/health/alert-thresholds ─────────────────────────────────


def test_put_self_always_allowed(client):
    wire(None, caller=ELDER)
    service = wire_thresholds()
    res = client.put(
        f"/api/health/alert-thresholds?user_id={ELDER}", json=PUT_PAYLOAD
    )
    assert res.status_code == 200
    assert service.update_calls == [(ELDER, ELDER)]
    assert res.json()["updated_by"] == ELDER


def test_put_allowed_for_guardian_records_updated_by_as_the_operator(client):
    """主要照顧者代為設定（health-alerts spec「主要照顧者代為設定」）：
    200，且最後修改者記為該 GUARDIAN，不是被照顧者本人。"""
    wire("GUARDIAN")
    service = wire_thresholds()
    res = client.put(
        f"/api/health/alert-thresholds?user_id={ELDER}", json=PUT_PAYLOAD
    )
    assert res.status_code == 200
    assert service.update_calls == [(ELDER, ME)]
    assert res.json()["updated_by"] == ME


def test_put_denied_for_caregiver(client):
    """協助照顧者設定被拒（health-alerts spec「協助照顧者設定被拒」）：
    CAREGIVER 只有 SENSITIVE 讀取權。"""
    wire("CAREGIVER")
    service = wire_thresholds()
    res = client.put(
        f"/api/health/alert-thresholds?user_id={ELDER}", json=PUT_PAYLOAD
    )
    assert res.status_code == 403
    assert service.update_calls == []


def test_put_denied_for_member(client):
    wire("MEMBER")
    service = wire_thresholds()
    res = client.put(
        f"/api/health/alert-thresholds?user_id={ELDER}", json=PUT_PAYLOAD
    )
    assert res.status_code == 403
    assert service.update_calls == []


def test_put_denied_for_stranger(client):
    wire(None)
    service = wire_thresholds()
    res = client.put(
        f"/api/health/alert-thresholds?user_id={ELDER}", json=PUT_PAYLOAD
    )
    assert res.status_code == 403
    assert service.update_calls == []


def test_put_denied_for_caregiver_even_in_shadow_mode(client):
    """影子模式下 CAREGIVER 寫入仍是 403：這條路徑導入前不存在，沒有
    「維持既有行為」可言。"""
    wire("CAREGIVER", state="shadow")
    service = wire_thresholds()
    res = client.put(
        f"/api/health/alert-thresholds?user_id={ELDER}", json=PUT_PAYLOAD
    )
    assert res.status_code == 403
    assert service.update_calls == []


def test_put_denied_for_member_even_in_shadow_mode(client):
    wire("MEMBER", state="shadow")
    service = wire_thresholds()
    res = client.put(
        f"/api/health/alert-thresholds?user_id={ELDER}", json=PUT_PAYLOAD
    )
    assert res.status_code == 403
    assert service.update_calls == []


def test_put_denied_for_stranger_even_in_shadow_mode(client):
    wire(None, state="shadow")
    service = wire_thresholds()
    res = client.put(
        f"/api/health/alert-thresholds?user_id={ELDER}", json=PUT_PAYLOAD
    )
    assert res.status_code == 403
    assert service.update_calls == []


def test_put_rejects_invalid_range_with_422_before_reaching_the_service(client):
    """上限不大於下限（health-alerts spec「上限不大於下限」）：422，
    SHALL NOT 寫入。"""
    wire("GUARDIAN")
    service = wire_thresholds()
    res = client.put(
        f"/api/health/alert-thresholds?user_id={ELDER}",
        json={"systolic_high": 100, "systolic_low": 120},
    )
    assert res.status_code == 422
    assert service.update_calls == []


# ── POST／GET／DELETE /api/health/measurements ───────────────────────
#
# health-measurements spec「代為記錄」「查看紀錄」「刪除紀錄」：五種身分
# （本人、GUARDIAN、CAREGIVER、MEMBER、非家人）× 三支端點，enforced 與
# shadow 兩種遷移狀態皆驗證——這是本 change 新增的能力，`has_legacy_
# equivalent=False`，shadow 模式 SHALL NOT 放寬任何一種身分。


class _FakeMeasurementService:
    """假的服務層，記錄呼叫；用來確認 403／404 擋在資料存取之前。"""

    def __init__(self, existing: Optional[HealthMeasurement] = None):
        self.existing = existing
        self.create_calls: list[tuple[str, str]] = []
        self.list_calls: list[str] = []
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []

    async def create(self, user_id: str, recorded_by: str, request):
        self.create_calls.append((user_id, recorded_by))
        # 依實際收到的請求型別回應，而不是寫死血壓——用來證明 Union body 真的
        # 解析成正確的模型（見下方「混合種類／未知欄位一律 422」的測試）。
        if isinstance(request, CreateBloodPressureRequest):
            return HealthMeasurement(
                id="M1",
                user_id=user_id,
                kind="blood_pressure",
                measured_at=NOW,
                recorded_by=recorded_by,
                systolic=request.systolic,
                diastolic=request.diastolic,
                pulse=request.pulse,
                level="within_range",
            )
        return HealthMeasurement(
            id="M1",
            user_id=user_id,
            kind="blood_glucose",
            measured_at=NOW,
            recorded_by=recorded_by,
            glucose_mg_dl=request.glucose_mg_dl,
            meal_context=request.meal_context,
            level="within_range",
        )

    async def list(self, user_id: str, kind=None, start=None, end=None):
        self.list_calls.append(user_id)
        return [self.existing] if self.existing is not None else []

    async def get(self, measurement_id: str):
        self.get_calls.append(measurement_id)
        if self.existing is None or self.existing.id != measurement_id:
            raise HTTPException(status_code=404, detail="找不到該筆紀錄")
        return self.existing

    async def delete(self, measurement_id: str):
        self.delete_calls.append(measurement_id)


def wire_measurements(existing: Optional[HealthMeasurement] = None):
    service = _FakeMeasurementService(existing)
    app.dependency_overrides[get_health_measurement_service] = lambda: service
    return service


POST_MEASUREMENT_PAYLOAD = {"systolic": 128, "diastolic": 82}

_EXISTING_MEASUREMENT = HealthMeasurement(
    id="M1",
    user_id=ELDER,
    kind="blood_pressure",
    measured_at=NOW,
    recorded_by=ELDER,
    systolic=128,
    diastolic=82,
    level="within_range",
)


# -- POST /api/health/measurements ------------------------------------


def test_post_measurement_self_always_allowed_without_any_family_relation(client):
    wire(None, caller=ELDER)
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}", json=POST_MEASUREMENT_PAYLOAD
    )
    assert res.status_code == 201
    body = res.json()
    assert body["id"] == "M1"
    assert body["recorded_by"] == ELDER
    assert body["level"] == "within_range"
    assert service.create_calls == [(ELDER, ELDER)]


def test_post_measurement_allowed_for_guardian_records_recorded_by_as_the_operator(client):
    """主要照顧者代記（spec「主要照顧者代記」）：201，記錄者為該 GUARDIAN。"""
    wire("GUARDIAN")
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}", json=POST_MEASUREMENT_PAYLOAD
    )
    assert res.status_code == 201
    assert res.json()["recorded_by"] == ME
    assert service.create_calls == [(ELDER, ME)]


def test_post_measurement_valid_blood_glucose_payload_resolves_to_glucose_and_returns_201(
    client,
):
    """有效的血糖 body 仍能正確解析成 ``CreateBloodGlucoseRequest``、回 201
    ——確認禁止多餘欄位（見下方兩支測試）沒有連帶弄壞正常的血糖請求。"""
    wire(None, caller=ELDER)
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}",
        json={"glucose_mg_dl": 112, "meal_context": "fasting"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["kind"] == "blood_glucose"
    assert body["glucose_mg_dl"] == 112
    assert body["meal_context"] == "fasting"


def test_post_measurement_rejects_mixed_kind_payload_with_422(client):
    """同時帶血壓與血糖欄位：兩個模型都因為多餘欄位驗證不過，SHALL 回 422，
    而不是被其中一個模型悄悄吃下、丟棄看不懂的欄位、驗證成功成錯的種類。"""
    wire(None, caller=ELDER)
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}",
        json={
            "systolic": 128,
            "diastolic": 82,
            "glucose_mg_dl": 100,
            "meal_context": "fasting",
        },
    )
    assert res.status_code == 422
    assert service.create_calls == []


def test_post_measurement_rejects_unknown_field_with_422(client):
    """打錯字或不相關的欄位：兩個模型皆驗證不過，SHALL 回 422，而不是被
    悄悄忽略、以血壓（或血糖）種類靜默成功。"""
    wire(None, caller=ELDER)
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}",
        json={"systolic": 128, "diastolic": 82, "systolic_typo": 128},
    )
    assert res.status_code == 422
    assert service.create_calls == []


def test_post_measurement_rejects_invalid_body_with_422_before_reaching_the_service(client):
    """收縮壓不大於舒張壓（spec「收縮壓不大於舒張壓」）：透過真實端點送出
    不合法的 body，SHALL 回 422、SHALL NOT 呼叫服務層——這是本檔案第一支
    直接對活端點送不合法 body 的測試，其餘輸入邊界已在
    tests/unit/models/test_health_models.py 窮舉過。"""
    wire(None, caller=ELDER)
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}",
        json={"systolic": 80, "diastolic": 120},
    )
    assert res.status_code == 422
    assert service.create_calls == []


def test_post_measurement_denied_for_caregiver(client):
    """協助照顧者代記被拒（spec「協助照顧者代記被拒」）。"""
    wire("CAREGIVER")
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}", json=POST_MEASUREMENT_PAYLOAD
    )
    assert res.status_code == 403
    assert service.create_calls == []


def test_post_measurement_denied_for_member(client):
    """一般家人代記被拒（spec「一般家人代記被拒」）。"""
    wire("MEMBER")
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}", json=POST_MEASUREMENT_PAYLOAD
    )
    assert res.status_code == 403
    assert service.create_calls == []


def test_post_measurement_denied_for_stranger(client):
    """非家人代記被拒（spec「非家人代記被拒」）。"""
    wire(None)
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}", json=POST_MEASUREMENT_PAYLOAD
    )
    assert res.status_code == 403
    assert service.create_calls == []


def test_post_measurement_denied_for_member_even_in_shadow_mode(client):
    """影子模式不放寬（spec「影子模式不放寬」）：一般家人代記仍是 403。"""
    wire("MEMBER", state="shadow")
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}", json=POST_MEASUREMENT_PAYLOAD
    )
    assert res.status_code == 403
    assert service.create_calls == []


def test_post_measurement_denied_for_caregiver_even_in_shadow_mode(client):
    wire("CAREGIVER", state="shadow")
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}", json=POST_MEASUREMENT_PAYLOAD
    )
    assert res.status_code == 403
    assert service.create_calls == []


def test_post_measurement_denied_for_stranger_even_in_shadow_mode(client):
    wire(None, state="shadow")
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}", json=POST_MEASUREMENT_PAYLOAD
    )
    assert res.status_code == 403
    assert service.create_calls == []


def test_post_measurement_self_allowed_even_in_shadow_mode(client):
    """影子模式不影響本人：本人代記自己一律放行，enforced／shadow 結果相同。"""
    wire(None, caller=ELDER, state="shadow")
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}", json=POST_MEASUREMENT_PAYLOAD
    )
    assert res.status_code == 201
    assert service.create_calls == [(ELDER, ELDER)]


def test_post_measurement_allowed_for_guardian_even_in_shadow_mode(client):
    """新增的能力不受影子模式放寬，但也 SHALL NOT 被誤放窄：矩陣本來就允許
    GUARDIAN 代記，enforced／shadow 兩種狀態下都該是 201，不是只有 enforced
    才通。"""
    wire("GUARDIAN", state="shadow")
    service = wire_measurements()
    res = client.post(
        f"/api/health/measurements?user_id={ELDER}", json=POST_MEASUREMENT_PAYLOAD
    )
    assert res.status_code == 201
    assert service.create_calls == [(ELDER, ME)]


# -- GET /api/health/measurements --------------------------------------


def test_get_measurements_self_always_allowed_without_any_family_relation(client):
    wire(None, caller=ELDER)
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.get(f"/api/health/measurements?user_id={ELDER}")
    assert res.status_code == 200
    assert service.list_calls == [ELDER]
    assert res.json()[0]["id"] == "M1"


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER"])
def test_get_measurements_allowed_for_sensitive_readers(client, role):
    """協助照顧者查看（spec「協助照顧者查看」）：GUARDIAN、CAREGIVER 皆可。"""
    wire(role)
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.get(f"/api/health/measurements?user_id={ELDER}")
    assert res.status_code == 200
    assert service.list_calls == [ELDER]


def test_get_measurements_denied_for_member(client):
    """一般家人查看被拒（spec「一般家人查看被拒」）。"""
    wire("MEMBER")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.get(f"/api/health/measurements?user_id={ELDER}")
    assert res.status_code == 403
    assert service.list_calls == []


def test_get_measurements_denied_for_stranger(client):
    """非家人查看被拒（spec「非家人查看被拒」）。"""
    wire(None)
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.get(f"/api/health/measurements?user_id={ELDER}")
    assert res.status_code == 403
    assert service.list_calls == []


def test_get_measurements_denied_for_member_even_in_shadow_mode(client):
    """影子模式下的一般家人（spec「影子模式下的一般家人」）：查詢仍是 403。"""
    wire("MEMBER", state="shadow")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.get(f"/api/health/measurements?user_id={ELDER}")
    assert res.status_code == 403
    assert service.list_calls == []


def test_get_measurements_denied_for_stranger_even_in_shadow_mode(client):
    wire(None, state="shadow")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.get(f"/api/health/measurements?user_id={ELDER}")
    assert res.status_code == 403
    assert service.list_calls == []


def test_get_measurements_self_allowed_even_in_shadow_mode(client):
    wire(None, caller=ELDER, state="shadow")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.get(f"/api/health/measurements?user_id={ELDER}")
    assert res.status_code == 200
    assert service.list_calls == [ELDER]


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER"])
def test_get_measurements_allowed_for_sensitive_readers_even_in_shadow_mode(client, role):
    """矩陣允許的讀取權（GUARDIAN、CAREGIVER）在 shadow 狀態下也該放行，不是
    只有 enforced 才通——影子模式只是不放寬，不是額外收緊。"""
    wire(role, state="shadow")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.get(f"/api/health/measurements?user_id={ELDER}")
    assert res.status_code == 200
    assert service.list_calls == [ELDER]


# -- DELETE /api/health/measurements/{id} ------------------------------


def test_delete_measurement_self_allowed(client):
    """本人刪除（spec「本人刪除」）：204。"""
    wire(None, caller=ELDER)
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.delete("/api/health/measurements/M1")
    assert res.status_code == 204
    assert service.delete_calls == ["M1"]


def test_delete_measurement_allowed_for_guardian(client):
    wire("GUARDIAN")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.delete("/api/health/measurements/M1")
    assert res.status_code == 204
    assert service.delete_calls == ["M1"]


def test_delete_measurement_denied_for_caregiver(client):
    """協助照顧者刪除被拒（spec「協助照顧者刪除被拒」）：403，紀錄保留。"""
    wire("CAREGIVER")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.delete("/api/health/measurements/M1")
    assert res.status_code == 403
    assert service.delete_calls == []


def test_delete_measurement_denied_for_member(client):
    wire("MEMBER")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.delete("/api/health/measurements/M1")
    assert res.status_code == 403
    assert service.delete_calls == []


def test_delete_measurement_denied_for_stranger(client):
    wire(None)
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.delete("/api/health/measurements/M1")
    assert res.status_code == 403
    assert service.delete_calls == []


def test_delete_measurement_denied_for_caregiver_even_in_shadow_mode(client):
    """影子模式下 CAREGIVER 刪除仍是 403（tasks.md 4.2：這條路徑導入前不
    存在，沒有「維持既有行為」可言）。"""
    wire("CAREGIVER", state="shadow")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.delete("/api/health/measurements/M1")
    assert res.status_code == 403
    assert service.delete_calls == []


def test_delete_measurement_denied_for_member_even_in_shadow_mode(client):
    wire("MEMBER", state="shadow")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.delete("/api/health/measurements/M1")
    assert res.status_code == 403
    assert service.delete_calls == []


def test_delete_measurement_denied_for_stranger_even_in_shadow_mode(client):
    wire(None, state="shadow")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.delete("/api/health/measurements/M1")
    assert res.status_code == 403
    assert service.delete_calls == []


def test_delete_measurement_self_allowed_even_in_shadow_mode(client):
    wire(None, caller=ELDER, state="shadow")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.delete("/api/health/measurements/M1")
    assert res.status_code == 204
    assert service.delete_calls == ["M1"]


def test_delete_measurement_allowed_for_guardian_even_in_shadow_mode(client):
    """矩陣允許的寫入權（GUARDIAN）在 shadow 狀態下也該放行——影子模式只是
    不放寬既有能力所沒有的角色，不是額外收緊矩陣本來就允許的角色。"""
    wire("GUARDIAN", state="shadow")
    service = wire_measurements(existing=_EXISTING_MEASUREMENT)
    res = client.delete("/api/health/measurements/M1")
    assert res.status_code == 204
    assert service.delete_calls == ["M1"]


def test_delete_measurement_returns_404_before_any_authorization_when_missing(client):
    """紀錄不存在時 SHALL 回 404（spec「刪除紀錄」）——即使操作者不是本人，
    存在性判定在授權之前，避免用 403 與 404 的差異探測他人紀錄是否存在。"""
    wire("MEMBER")
    service = wire_measurements(existing=None)
    res = client.delete("/api/health/measurements/does-not-exist")
    assert res.status_code == 404
    assert service.delete_calls == []


# ── POST／GET／PATCH／DELETE /api/health/menstrual ────────────────────
#
# menstrual-cycle-log spec「經期資料不跨使用者呈現」：任何以他人識別碼的
# 請求一律 403，不論操作者的角色、委任狀態或家庭的遷移狀態——這是 PERSONAL
# 分類的規則，SHALL NOT 經過 FamilyAuthorizationService 的矩陣判定（見
# app/routers/users/health.py 該區塊的說明；constraints.md「Menstrual」）。
# 底下用 GUARDIAN、有效委任者、shadow 遷移狀態三種「換成別的資源就會放行」
# 的身分，證明這支端點完全不吃那一套。


class _ActiveDelegation:
    """一個對 ELDER 持有**真正有效**委任的假 repository——用來證明就算操作者
    對其他資源（例如 SENSITIVE）有完整權限，經期仍然 403：委任只是另一種
    「換算成角色」的管道，PERSONAL 對任何換算出來的角色都是空集合。"""

    async def has_active_delegation(self, owner_id, delegate_user_id, now=None):
        return owner_id == ELDER and delegate_user_id == ME


def wire_guardian(state="enforced", caller=ME):
    wire("GUARDIAN", state=state, caller=caller)


def wire_active_delegate(caller=ME, target=ELDER, state="enforced"):
    """操作者對 target 持有有效委任（委任預設解析為 GUARDIAN 等級）。"""
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id=caller
    )
    trees = {
        target: FamilyTree(
            user_id=target,
            family_members=[],
            rbac_migration_state=state,
            created_at=NOW,
            updated_at=NOW,
        )
    }
    app.dependency_overrides[get_family_authorization_service] = (
        lambda: FamilyAuthorizationService(
            family_tree_repository=_Trees(trees),
            delegation_repository=_ActiveDelegation(),
            enforcement_enabled=True,
        )
    )


def wire_shadow_guardian(caller=ME):
    wire("GUARDIAN", state="shadow", caller=caller)


# 三種在其他資源都會放行、但經期 SHALL 一律 403 的身分設定。
_CROSS_USER_IDENTITIES = [
    pytest.param(wire_guardian, id="guardian"),
    pytest.param(wire_active_delegate, id="active_delegate"),
    pytest.param(wire_shadow_guardian, id="shadow_mode_guardian"),
]


class _FakeMenstrualService:
    """假的服務層，記錄呼叫；用來確認 403／404 擋在資料存取之前，而且完全
    不依賴 FamilyAuthorizationService 的判定結果。"""

    def __init__(
        self,
        existing: Optional[MenstrualRecord] = None,
        gender_ok: bool = True,
    ):
        self.existing = existing
        self.gender_ok = gender_ok
        self.create_calls: list[str] = []
        self.list_calls: list[str] = []
        self.get_calls: list[str] = []
        self.update_calls: list[str] = []
        self.delete_calls: list[str] = []

    async def create(self, user_id: str, request):
        self.create_calls.append(user_id)
        if not self.gender_ok:
            raise HTTPException(
                status_code=403,
                detail="請先至個人健康頁設定性別為女性，才能新增經期紀錄。",
            )
        return MenstrualRecord(
            id="R1",
            user_id=user_id,
            start_date=request.start_date,
            end_date=request.end_date,
            flow=request.flow,
            note=request.note,
        )

    async def list(self, user_id: str):
        self.list_calls.append(user_id)
        return [self.existing] if self.existing is not None else []

    async def get(self, record_id: str):
        self.get_calls.append(record_id)
        if self.existing is None or self.existing.id != record_id:
            raise HTTPException(status_code=404, detail="找不到該筆紀錄")
        return self.existing

    async def update(self, record_id: str, request):
        self.update_calls.append(record_id)
        return self.existing.model_copy(
            update=request.model_dump(exclude_unset=True)
        )

    async def delete(self, record_id: str):
        self.delete_calls.append(record_id)


def wire_menstrual(existing: Optional[MenstrualRecord] = None, gender_ok: bool = True):
    service = _FakeMenstrualService(existing, gender_ok=gender_ok)
    app.dependency_overrides[get_menstrual_record_service] = lambda: service
    return service


_EXISTING_MENSTRUAL_RECORD = MenstrualRecord(
    id="R1", user_id=ELDER, start_date="2026-09-01"
)


# -- POST /api/health/menstrual -----------------------------------------


def test_post_menstrual_self_allowed_without_any_family_relation(client):
    wire(None, caller=ELDER)
    service = wire_menstrual()
    res = client.post(
        f"/api/health/menstrual?user_id={ELDER}", json={"start_date": "2026-09-01"}
    )
    assert res.status_code == 201
    assert service.create_calls == [ELDER]


def test_post_menstrual_rejects_non_female_gender_with_403(client):
    """性別未填或非女性（spec「性別未填」）：403，訊息說明需先設定性別——
    這裡驗證服務層拋出的 403 有正確傳到 HTTP 回應，不是被吞掉或改寫。"""
    wire(None, caller=ELDER)
    service = wire_menstrual(gender_ok=False)
    res = client.post(
        f"/api/health/menstrual?user_id={ELDER}", json={"start_date": "2026-09-01"}
    )
    assert res.status_code == 403
    assert "性別" in res.json()["detail"]


@pytest.mark.parametrize("wire_fn", _CROSS_USER_IDENTITIES)
def test_post_menstrual_denied_for_another_users_id_regardless_of_role(client, wire_fn):
    """以他人識別碼查詢／新增（spec「以他人識別碼查詢」）：不論角色、委任
    或遷移狀態一律 403，SHALL NOT 寫入。"""
    wire_fn()
    service = wire_menstrual()
    res = client.post(
        f"/api/health/menstrual?user_id={ELDER}", json={"start_date": "2026-09-01"}
    )
    assert res.status_code == 403
    assert service.create_calls == []


def test_post_menstrual_denied_for_stranger(client):
    wire(None)
    service = wire_menstrual()
    res = client.post(
        f"/api/health/menstrual?user_id={ELDER}", json={"start_date": "2026-09-01"}
    )
    assert res.status_code == 403
    assert service.create_calls == []


# -- GET /api/health/menstrual --------------------------------------------


def test_get_menstrual_self_allowed_without_any_family_relation(client):
    wire(None, caller=ELDER)
    service = wire_menstrual(existing=_EXISTING_MENSTRUAL_RECORD)
    res = client.get(f"/api/health/menstrual?user_id={ELDER}")
    assert res.status_code == 200
    assert service.list_calls == [ELDER]
    assert res.json()[0]["id"] == "R1"


def test_get_menstrual_self_via_omitted_user_id(client):
    wire(None, caller=ELDER)
    service = wire_menstrual(existing=_EXISTING_MENSTRUAL_RECORD)
    res = client.get("/api/health/menstrual")
    assert res.status_code == 200
    assert service.list_calls == [ELDER]


@pytest.mark.parametrize("wire_fn", _CROSS_USER_IDENTITIES)
def test_get_menstrual_denied_for_another_users_id_regardless_of_role(client, wire_fn):
    wire_fn()
    service = wire_menstrual(existing=_EXISTING_MENSTRUAL_RECORD)
    res = client.get(f"/api/health/menstrual?user_id={ELDER}")
    assert res.status_code == 403
    assert service.list_calls == []


def test_get_menstrual_denied_for_stranger(client):
    wire(None)
    service = wire_menstrual(existing=_EXISTING_MENSTRUAL_RECORD)
    res = client.get(f"/api/health/menstrual?user_id={ELDER}")
    assert res.status_code == 403
    assert service.list_calls == []


# -- PATCH /api/health/menstrual/{id} --------------------------------------


def test_patch_menstrual_self_allowed(client):
    wire(None, caller=ELDER)
    service = wire_menstrual(existing=_EXISTING_MENSTRUAL_RECORD)
    res = client.patch(
        "/api/health/menstrual/R1", json={"end_date": "2026-09-05"}
    )
    assert res.status_code == 200
    assert service.update_calls == ["R1"]


@pytest.mark.parametrize("wire_fn", _CROSS_USER_IDENTITIES)
def test_patch_menstrual_denied_for_a_record_owned_by_someone_else(client, wire_fn):
    """PATCH 一筆屬於他人的紀錄：403，SHALL NOT 修改、也 SHALL NOT 揭露
    內容（dispatch notes「PATCH／DELETE」）。"""
    wire_fn()
    service = wire_menstrual(existing=_EXISTING_MENSTRUAL_RECORD)
    res = client.patch(
        "/api/health/menstrual/R1", json={"end_date": "2026-09-05"}
    )
    assert res.status_code == 403
    assert service.update_calls == []


def test_patch_menstrual_returns_404_before_identity_check_when_missing(client):
    """紀錄不存在時 SHALL 回 404，即使操作者不是本人——同量測「刪除紀錄」
    的理由：存在性判定在先，避免用 403／404 的差異探測他人紀錄是否存在。"""
    wire("GUARDIAN")
    service = wire_menstrual(existing=None)
    res = client.patch(
        "/api/health/menstrual/does-not-exist", json={"end_date": "2026-09-05"}
    )
    assert res.status_code == 404
    assert service.update_calls == []


# -- DELETE /api/health/menstrual/{id} -------------------------------------


def test_delete_menstrual_self_allowed(client):
    wire(None, caller=ELDER)
    service = wire_menstrual(existing=_EXISTING_MENSTRUAL_RECORD)
    res = client.delete("/api/health/menstrual/R1")
    assert res.status_code == 204
    assert service.delete_calls == ["R1"]


@pytest.mark.parametrize("wire_fn", _CROSS_USER_IDENTITIES)
def test_delete_menstrual_denied_for_a_record_owned_by_someone_else(client, wire_fn):
    wire_fn()
    service = wire_menstrual(existing=_EXISTING_MENSTRUAL_RECORD)
    res = client.delete("/api/health/menstrual/R1")
    assert res.status_code == 403
    assert service.delete_calls == []


def test_delete_menstrual_returns_404_before_identity_check_when_missing(client):
    wire("GUARDIAN")
    service = wire_menstrual(existing=None)
    res = client.delete("/api/health/menstrual/does-not-exist")
    assert res.status_code == 404
    assert service.delete_calls == []


# ── PUT／GET /api/health/steps ───────────────────────────────────────────
#
# step-counter spec「只有本人可以寫入」「家人查看步數」。寫入的授權原則與
# 經期相同：以他人識別碼寫入一律 403，不經 FamilyAuthorizationService——
# 步數只能來自本人手機的感測器，不存在「代為走路」，沒有任何角色能通過
# （見 app/routers/users/health.py 該區塊的說明）。讀取則與量測、提醒範圍
# 相同：他人查看需 SENSITIVE READ（GUARDIAN、CAREGIVER 可看，MEMBER 與
# 非家人 403），`has_legacy_equivalent=False`，never mask()。


class _FakeStepService:
    """假的服務層，記錄呼叫；用來確認 403 擋在資料存取之前。"""

    def __init__(
        self,
        daily_total: Optional[StepCount] = None,
        totals: Optional[list] = None,
    ):
        self.daily_total = daily_total
        self.totals = totals if totals is not None else []
        self.sync_calls: list[tuple] = []
        self.list_calls: list[tuple] = []

    async def sync(self, user_id: str, session_id: str, request):
        self.sync_calls.append((user_id, session_id))
        return self.daily_total or StepCount(
            user_id=user_id, date="2026-08-24", steps=request.steps
        )

    async def list_daily_totals(self, user_id: str, start=None, end=None):
        self.list_calls.append((user_id, start, end))
        return self.totals


def wire_steps(daily_total: Optional[StepCount] = None, totals: Optional[list] = None):
    service = _FakeStepService(daily_total=daily_total, totals=totals)
    app.dependency_overrides[get_step_service] = lambda: service
    return service


VALID_SESSION_ID = "8f14e45f-ceea-4c9c-8f77-4f3c3e2b2b1a"
PUT_STEP_PAYLOAD = {"steps": 300, "started_at": "2026-08-24T11:55:00+00:00"}


# -- PUT /api/health/steps/sessions/{session_id} ------------------------


def test_put_step_session_self_allowed_without_any_family_relation(client):
    wire(None, caller=ELDER)
    service = wire_steps()
    res = client.put(
        f"/api/health/steps/sessions/{VALID_SESSION_ID}?user_id={ELDER}",
        json=PUT_STEP_PAYLOAD,
    )
    assert res.status_code == 200
    assert service.sync_calls == [(ELDER, VALID_SESSION_ID)]


def test_put_step_session_self_via_omitted_user_id(client):
    wire(None, caller=ELDER)
    service = wire_steps()
    res = client.put(
        f"/api/health/steps/sessions/{VALID_SESSION_ID}", json=PUT_STEP_PAYLOAD
    )
    assert res.status_code == 200
    assert service.sync_calls == [(ELDER, VALID_SESSION_ID)]


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER", "MEMBER"])
def test_put_step_session_denied_for_any_family_role(client, role):
    """spec「主要照顧者代為回報」：任何角色（包含對其他資源有完整讀寫權的
    GUARDIAN）代為回報步數一律 403，SHALL NOT 寫入。"""
    wire(role)
    service = wire_steps()
    res = client.put(
        f"/api/health/steps/sessions/{VALID_SESSION_ID}?user_id={ELDER}",
        json=PUT_STEP_PAYLOAD,
    )
    assert res.status_code == 403
    assert service.sync_calls == []


def test_put_step_session_denied_for_stranger(client):
    wire(None)
    service = wire_steps()
    res = client.put(
        f"/api/health/steps/sessions/{VALID_SESSION_ID}?user_id={ELDER}",
        json=PUT_STEP_PAYLOAD,
    )
    assert res.status_code == 403
    assert service.sync_calls == []


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER", "MEMBER"])
def test_put_step_session_denied_for_any_family_role_even_in_shadow_mode(client, role):
    """代為回報不受影子模式放寬——這條規則不是家庭矩陣的一格，沒有「影子
    模式維持既有行為」可言（同經期端點的理由）。"""
    wire(role, state="shadow")
    service = wire_steps()
    res = client.put(
        f"/api/health/steps/sessions/{VALID_SESSION_ID}?user_id={ELDER}",
        json=PUT_STEP_PAYLOAD,
    )
    assert res.status_code == 403
    assert service.sync_calls == []


def test_put_step_session_self_allowed_even_in_shadow_mode(client):
    wire(None, caller=ELDER, state="shadow")
    service = wire_steps()
    res = client.put(
        f"/api/health/steps/sessions/{VALID_SESSION_ID}?user_id={ELDER}",
        json=PUT_STEP_PAYLOAD,
    )
    assert res.status_code == 200
    assert service.sync_calls == [(ELDER, VALID_SESSION_ID)]


def test_put_step_session_rejects_non_uuid_v4_session_id_with_422(client):
    """session_id 路徑參數須為 UUID v4（step-counter spec「合理性檢查」；
    constraints.md「Steps」）：非 UUID 一律 422，SHALL NOT 呼叫服務層。"""
    wire(None, caller=ELDER)
    service = wire_steps()
    res = client.put(
        "/api/health/steps/sessions/not-a-uuid", json=PUT_STEP_PAYLOAD
    )
    assert res.status_code == 422
    assert service.sync_calls == []


def test_put_step_session_rejects_uuid_v1_session_id_with_422(client):
    # UUID v1（時間戳版本），版本欄位不是 4，SHALL 被拒絕。
    wire(None, caller=ELDER)
    service = wire_steps()
    res = client.put(
        "/api/health/steps/sessions/2ed6657d-e927-11e6-94ba-8b2ba76b2efe",
        json=PUT_STEP_PAYLOAD,
    )
    assert res.status_code == 422
    assert service.sync_calls == []


def test_put_step_session_rejects_negative_steps_with_422_before_reaching_the_service(
    client,
):
    wire(None, caller=ELDER)
    service = wire_steps()
    res = client.put(
        f"/api/health/steps/sessions/{VALID_SESSION_ID}",
        json={"steps": -1, "started_at": "2026-08-24T11:55:00+00:00"},
    )
    assert res.status_code == 422
    assert service.sync_calls == []


def test_put_step_session_returns_the_daily_total_from_the_service(client):
    wire(None, caller=ELDER)
    service = wire_steps(daily_total=StepCount(user_id=ELDER, date="2026-08-24", steps=800))
    res = client.put(
        f"/api/health/steps/sessions/{VALID_SESSION_ID}", json=PUT_STEP_PAYLOAD
    )
    assert res.status_code == 200
    body = res.json()
    assert body == {"user_id": ELDER, "date": "2026-08-24", "steps": 800}


# -- GET /api/health/steps ------------------------------------------------


def test_get_steps_self_allowed_without_any_family_relation(client):
    wire(None, caller=ELDER)
    service = wire_steps(totals=[StepCount(user_id=ELDER, date="2026-08-24", steps=800)])
    res = client.get(f"/api/health/steps?user_id={ELDER}")
    assert res.status_code == 200
    assert service.list_calls == [(ELDER, None, None)]
    assert res.json()[0]["steps"] == 800


def test_get_steps_self_via_omitted_user_id(client):
    wire(None, caller=ELDER)
    service = wire_steps()
    res = client.get("/api/health/steps")
    assert res.status_code == 200
    assert service.list_calls == [(ELDER, None, None)]


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER"])
def test_get_steps_allowed_for_sensitive_readers(client, role):
    """spec「協助照顧者查看今日步數」：GUARDIAN、CAREGIVER 皆可。"""
    wire(role)
    service = wire_steps(totals=[StepCount(user_id=ELDER, date="2026-08-24", steps=800)])
    res = client.get(f"/api/health/steps?user_id={ELDER}")
    assert res.status_code == 200
    assert service.list_calls == [(ELDER, None, None)]


def test_get_steps_denied_for_member(client):
    """spec「一般家人查看被拒」。"""
    wire("MEMBER")
    service = wire_steps()
    res = client.get(f"/api/health/steps?user_id={ELDER}")
    assert res.status_code == 403
    assert service.list_calls == []


def test_get_steps_denied_for_stranger(client):
    wire(None)
    service = wire_steps()
    res = client.get(f"/api/health/steps?user_id={ELDER}")
    assert res.status_code == 403
    assert service.list_calls == []


def test_get_steps_denied_for_member_even_in_shadow_mode(client):
    wire("MEMBER", state="shadow")
    service = wire_steps()
    res = client.get(f"/api/health/steps?user_id={ELDER}")
    assert res.status_code == 403
    assert service.list_calls == []


def test_get_steps_denied_for_stranger_even_in_shadow_mode(client):
    wire(None, state="shadow")
    service = wire_steps()
    res = client.get(f"/api/health/steps?user_id={ELDER}")
    assert res.status_code == 403
    assert service.list_calls == []


def test_get_steps_self_allowed_even_in_shadow_mode(client):
    wire(None, caller=ELDER, state="shadow")
    service = wire_steps()
    res = client.get(f"/api/health/steps?user_id={ELDER}")
    assert res.status_code == 200
    assert service.list_calls == [(ELDER, None, None)]


@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER"])
def test_get_steps_allowed_for_sensitive_readers_even_in_shadow_mode(client, role):
    wire(role, state="shadow")
    service = wire_steps()
    res = client.get(f"/api/health/steps?user_id={ELDER}")
    assert res.status_code == 200
    assert service.list_calls == [(ELDER, None, None)]


def test_get_steps_passes_through_explicit_start_and_end(client):
    wire(None, caller=ELDER)
    service = wire_steps()
    res = client.get("/api/health/steps?start=2026-08-01&end=2026-08-24")
    assert res.status_code == 200
    assert service.list_calls == [(ELDER, date(2026, 8, 1), date(2026, 8, 24))]
