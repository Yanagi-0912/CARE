from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_medication_service,
    get_prescription_scan_service,
    get_prescription_scan_enabled,
    require_prescription_scan_enabled,
)
from app.main import app
from app.models.medication import (
    Medication,
    MedicationLog,
    MedicationReminder,
    MedicationReminderWithMedications,
)
from app.models.prescription import (
    PrescriptionCommitResult,
    PrescriptionDraft,
    RecognitionResult,
    RecognizedDrug,
)
from app.services.medication.prescription_ocr_service import (
    PrescriptionNotRecognizedError,
    PrescriptionServiceUnavailableError,
    PrescriptionUnreadableError,
)
from app.services.medication.prescription_scan_service import (
    DraftExpiredError,
    DraftNotFoundError,
    SlotsRequiredError,
    TargetNotInFamilyError,
)

client = TestClient(app)


@pytest.fixture()
def override_current_user():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id="U_TEST_USER"
    )
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def prescription_scan_enabled():
    """覆寫功能開關 dependency 模擬「已開啟」，不去改動 settings 本身——
    settings 是整個行程共用的單例，直接改它的屬性會讓其他平行測試也看到。"""
    app.dependency_overrides[require_prescription_scan_enabled] = lambda: None
    yield
    app.dependency_overrides.pop(require_prescription_scan_enabled, None)


class FakePrescriptionScanService:
    """POST /prescription-scan、GET/POST /prescription-drafts/... 的替身。

    以建構子參數決定要回傳結果還是拋出例外，覆蓋路由層的每一種例外映射，
    不需要碰真正的 OCR、藥證庫或資料庫。"""

    def __init__(
        self,
        scan_result: PrescriptionDraft | None = None,
        scan_exception: Exception | None = None,
        draft_result: PrescriptionDraft | None = None,
        draft_exception: Exception | None = None,
        commit_result: PrescriptionCommitResult | None = None,
        commit_exception: Exception | None = None,
    ):
        self._scan_result = scan_result
        self._scan_exception = scan_exception
        self._draft_result = draft_result
        self._draft_exception = draft_exception
        self._commit_result = commit_result
        self._commit_exception = commit_exception
        self.scan_calls: list[tuple] = []
        self.commit_calls: list[tuple] = []

    async def scan(self, image_bytes: bytes, mime_type: str, user_id: str):
        self.scan_calls.append((image_bytes, mime_type, user_id))
        if self._scan_exception is not None:
            raise self._scan_exception
        return self._scan_result

    async def get_draft(self, draft_id: str, user_id: str):
        if self._draft_exception is not None:
            raise self._draft_exception
        return self._draft_result

    async def commit(self, draft_id: str, user_id: str, payload):
        self.commit_calls.append((draft_id, user_id, payload))
        if self._commit_exception is not None:
            raise self._commit_exception
        return self._commit_result


def _override_scan_service(fake: FakePrescriptionScanService) -> None:
    app.dependency_overrides[get_prescription_scan_service] = lambda: fake


def _draft(draft_id: str = "D1") -> PrescriptionDraft:
    return PrescriptionDraft(
        draft_id=draft_id,
        creator_user_id="U_TEST_USER",
        recognition=RecognitionResult(
            patient_name="王大明",
            drugs=[RecognizedDrug(name="脈優錠5毫克", frequency_code="QD")],
        ),
        confidence_level="medium",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
    )


def test_create_reminders_router(override_current_user):
    fake_reminder = MedicationReminder(
        creator_user_id="U_TEST_USER",
        user_id="U_TEST_USER",
        slot_type="morning",
        scheduled_time="08:00",
    )
    with patch(
        "app.services.medication.medication_service.MedicationService.create_reminders",
        new_callable=AsyncMock,
        return_value=[fake_reminder],
    ):
        response = client.post(
            "/api/medications/reminders",
            json={"user_id": "U_TEST_USER", "slots": ["morning"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["slot_type"] == "morning"


def test_get_reminders_router(override_current_user):
    """GET /reminders 改呼叫 get_user_reminders_with_medications；用
    app.dependency_overrides 換掉整個 service，而不是 patch 其中一個方法，
    才符合「router 測試一律以 dependency_overrides 注入替身」的規則。"""
    fake_reminder = MedicationReminder(
        creator_user_id="U_TEST_USER",
        user_id="U_TEST_USER",
        slot_type="evening",
        scheduled_time="18:00",
    )

    class _FakeMedicationService:
        async def get_user_reminders_with_medications(
            self, user_id, requester_user_id=None
        ):
            return [fake_reminder]

    app.dependency_overrides[get_medication_service] = lambda: _FakeMedicationService()
    try:
        response = client.get("/api/medications/reminders")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["slot_type"] == "evening"
        # medications 是新加的欄位；沒有 medication_ids 時應為空陣列，
        # 而不是缺席或 null。
        assert data[0]["medications"] == []
    finally:
        app.dependency_overrides.pop(get_medication_service, None)


def test_get_reminders_router_includes_resolved_medications(override_current_user):
    """7.4：GET /reminders 的回應新增 medications 欄位——驗證有 medication_ids
    時，回應裡真的帶著解析出來的藥品物件，而不只是原本的 id 陣列。"""
    fake_medication = Medication(
        id="M1",
        user_id="U_TEST_USER",
        created_by_user_id="U_TEST_USER",
        name="脈優錠5毫克",
    )
    fake_reminder = MedicationReminder(
        creator_user_id="U_TEST_USER",
        user_id="U_TEST_USER",
        slot_type="morning",
        scheduled_time="08:00",
        medication_ids=["M1"],
    )

    class _FakeMedicationService:
        async def get_user_reminders_with_medications(
            self, user_id, requester_user_id=None
        ):
            reminder_data = fake_reminder.model_dump(by_alias=True)
            reminder_data["medications"] = [fake_medication]
            return [MedicationReminderWithMedications(**reminder_data)]

    app.dependency_overrides[get_medication_service] = lambda: _FakeMedicationService()
    try:
        response = client.get("/api/medications/reminders")
        assert response.status_code == 200
        data = response.json()
        assert len(data[0]["medications"]) == 1
        assert data[0]["medications"][0]["name"] == "脈優錠5毫克"
    finally:
        app.dependency_overrides.pop(get_medication_service, None)


def test_get_created_reminders_router(override_current_user):
    """/reminders/created 查的是「誰設定的」，帶入的是登入者本人的 id。"""
    fake_reminder = MedicationReminder(
        creator_user_id="U_TEST_USER",
        user_id="U_FAMILY_MEMBER",
        slot_type="noon",
        scheduled_time="12:00",
    )
    with patch(
        "app.services.medication.medication_service.MedicationService.get_creator_reminders",
        new_callable=AsyncMock,
        return_value=[fake_reminder],
    ) as mock_service:
        response = client.get("/api/medications/reminders/created")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["user_id"] == "U_FAMILY_MEMBER"
        mock_service.assert_awaited_once_with(creator_user_id="U_TEST_USER")


# ── 藥袋辨識：功能開關 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method, path, kwargs",
    [
        (
            "post",
            "/api/medications/prescription-scan",
            {"files": {"file": ("bag.jpg", b"fake-bytes", "image/jpeg")}},
        ),
        ("get", "/api/medications/prescription-drafts/D1", {}),
        (
            "post",
            "/api/medications/prescription-drafts/D1/commit",
            {"json": {"user_id": "U_TEST_USER", "drugs": []}},
        ),
    ],
)
def test_prescription_endpoints_404_when_flag_disabled(
    override_current_user, method, path, kwargs
):
    """功能開關關閉時，三支端點都要表現得像不存在一樣。

    以 dependency_overrides 明確驅動「關閉」這個狀態，而不是依賴
    PRESCRIPTION_SCAN_ENABLED 當下的預設值——這條規則要被驗證的是行為，
    不是環境。先前這裡斷言預設值為 False，於是預設一翻成開啟，這個測試
    就會失敗，但失敗的原因跟它要守的規則無關。
    """
    app.dependency_overrides[get_prescription_scan_enabled] = lambda: False
    try:
        response = getattr(client, method)(path, **kwargs)
    finally:
        app.dependency_overrides.pop(get_prescription_scan_enabled, None)
    assert response.status_code == 404


# ── POST /prescription-scan ───────────────────────────────────────────


def test_scan_endpoint_returns_the_draft_on_success(
    override_current_user, prescription_scan_enabled
):
    draft = _draft()
    fake_service = FakePrescriptionScanService(scan_result=draft)
    _override_scan_service(fake_service)

    response = client.post(
        "/api/medications/prescription-scan",
        files={"file": ("bag.jpg", b"fake-bytes", "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.json()["draft_id"] == draft.draft_id
    image_bytes, mime_type, user_id = fake_service.scan_calls[0]
    assert image_bytes == b"fake-bytes"
    assert mime_type == "image/jpeg"
    assert user_id == "U_TEST_USER"


def test_scan_endpoint_415_when_content_type_is_not_image(
    override_current_user, prescription_scan_enabled
):
    fake_service = FakePrescriptionScanService()
    _override_scan_service(fake_service)

    response = client.post(
        "/api/medications/prescription-scan",
        files={"file": ("bag.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 415
    # 非影像 content type 連讀取都不必做，辨識服務不該被呼叫。
    assert fake_service.scan_calls == []


def test_scan_endpoint_413_when_image_exceeds_the_size_limit(
    override_current_user, prescription_scan_enabled
):
    fake_service = FakePrescriptionScanService()
    _override_scan_service(fake_service)
    oversized = b"x" * (settings.PRESCRIPTION_SCAN_MAX_IMAGE_BYTES + 1)

    response = client.post(
        "/api/medications/prescription-scan",
        files={"file": ("bag.jpg", oversized, "image/jpeg")},
    )

    assert response.status_code == 413
    # 超過大小上限時 SHALL NOT 呼叫辨識服務。
    assert fake_service.scan_calls == []


@pytest.mark.parametrize(
    "exception, expected_status, expected_reason",
    [
        (PrescriptionUnreadableError("影像模糊看不清楚"), 422, "unreadable"),
        (PrescriptionNotRecognizedError("影像中未辨識出任何藥品"), 422, "not_prescription"),
        (PrescriptionServiceUnavailableError("辨識服務逾時"), 503, "service_unavailable"),
    ],
)
def test_scan_endpoint_maps_each_failure_reason_distinctly(
    override_current_user,
    prescription_scan_enabled,
    exception,
    expected_status,
    expected_reason,
):
    fake_service = FakePrescriptionScanService(scan_exception=exception)
    _override_scan_service(fake_service)

    response = client.post(
        "/api/medications/prescription-scan",
        files={"file": ("bag.jpg", b"fake-bytes", "image/jpeg")},
    )

    assert response.status_code == expected_status
    assert response.json()["detail"]["reason"] == expected_reason


def test_scan_endpoint_unreadable_and_not_prescription_stay_distinguishable(
    override_current_user, prescription_scan_enabled
):
    """unreadable 與 not_prescription 共用同一個 HTTP 狀態碼（422）——
    reason 欄位才是唯一的區分依據，這裡直接驗證兩者的 reason 不相同，
    不能被收斂成同一則錯誤。"""
    fake_service = FakePrescriptionScanService(
        scan_exception=PrescriptionUnreadableError("看不清楚")
    )
    _override_scan_service(fake_service)
    unreadable_response = client.post(
        "/api/medications/prescription-scan",
        files={"file": ("bag.jpg", b"fake-bytes", "image/jpeg")},
    )

    fake_service._scan_exception = PrescriptionNotRecognizedError("不是藥袋")
    not_prescription_response = client.post(
        "/api/medications/prescription-scan",
        files={"file": ("bag.jpg", b"fake-bytes", "image/jpeg")},
    )

    assert unreadable_response.status_code == not_prescription_response.status_code == 422
    assert (
        unreadable_response.json()["detail"]["reason"]
        != not_prescription_response.json()["detail"]["reason"]
    )


# ── GET /prescription-drafts/{draft_id} ───────────────────────────────


def test_get_draft_endpoint_returns_the_draft(
    override_current_user, prescription_scan_enabled
):
    draft = _draft()
    fake_service = FakePrescriptionScanService(draft_result=draft)
    _override_scan_service(fake_service)

    response = client.get(f"/api/medications/prescription-drafts/{draft.draft_id}")

    assert response.status_code == 200
    assert response.json()["draft_id"] == draft.draft_id


def test_get_draft_endpoint_404_for_another_users_draft(
    override_current_user, prescription_scan_enabled
):
    """他人的 draft_id：service 一律拋 DraftNotFoundError（不區分「不存在」
    與「不屬於你」），路由層一律轉成 404，不能讓這支端點變成探測管道。"""
    fake_service = FakePrescriptionScanService(draft_exception=DraftNotFoundError("D1"))
    _override_scan_service(fake_service)

    response = client.get("/api/medications/prescription-drafts/D1")

    assert response.status_code == 404


# ── POST /prescription-drafts/{draft_id}/commit ───────────────────────


def test_commit_endpoint_returns_the_result(
    override_current_user, prescription_scan_enabled
):
    result = PrescriptionCommitResult(medication_ids=["M1"], prn_medication_ids=[])
    fake_service = FakePrescriptionScanService(commit_result=result)
    _override_scan_service(fake_service)

    response = client.post(
        "/api/medications/prescription-drafts/D1/commit",
        json={"user_id": "U_TEST_USER", "drugs": []},
    )

    assert response.status_code == 200
    assert response.json()["medication_ids"] == ["M1"]


def test_commit_endpoint_404_for_another_users_draft(
    override_current_user, prescription_scan_enabled
):
    fake_service = FakePrescriptionScanService(commit_exception=DraftNotFoundError("D1"))
    _override_scan_service(fake_service)

    response = client.post(
        "/api/medications/prescription-drafts/D1/commit",
        json={"user_id": "U_TEST_USER", "drugs": []},
    )

    assert response.status_code == 404


def test_commit_endpoint_410_when_draft_expired(
    override_current_user, prescription_scan_enabled
):
    fake_service = FakePrescriptionScanService(commit_exception=DraftExpiredError("D1"))
    _override_scan_service(fake_service)

    response = client.post(
        "/api/medications/prescription-drafts/D1/commit",
        json={"user_id": "U_TEST_USER", "drugs": []},
    )

    assert response.status_code == 410


def test_commit_endpoint_400_when_target_not_in_family(
    override_current_user, prescription_scan_enabled
):
    fake_service = FakePrescriptionScanService(
        commit_exception=TargetNotInFamilyError("U_STRANGER")
    )
    _override_scan_service(fake_service)

    response = client.post(
        "/api/medications/prescription-drafts/D1/commit",
        json={"user_id": "U_STRANGER", "drugs": []},
    )

    assert response.status_code == 400


def test_commit_endpoint_400_when_slots_required(
    override_current_user, prescription_scan_enabled
):
    fake_service = FakePrescriptionScanService(
        commit_exception=SlotsRequiredError("某藥")
    )
    _override_scan_service(fake_service)

    response = client.post(
        "/api/medications/prescription-drafts/D1/commit",
        json={"user_id": "U_TEST_USER", "drugs": []},
    )

    assert response.status_code == 400
    assert "某藥" in response.json()["detail"]


def test_confirm_medication_router(override_current_user):
    fake_log = MedicationLog(
        reminder_id="R123",
        user_id="U_TEST_USER",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-07-26T08:00:00Z",
        timeout_at="2026-07-26T08:30:00Z",
        status="taken",
    )
    with patch(
        "app.services.medication.medication_service.MedicationService.confirm_medication",
        new_callable=AsyncMock,
        return_value=fake_log,
    ):
        response = client.post("/api/medications/confirm/L123")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "taken"
