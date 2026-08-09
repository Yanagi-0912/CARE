from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from app.dependencies import CurrentUser, get_current_user
from app.main import app
from app.models.medication import MedicationLog, MedicationReminder

client = TestClient(app)


@pytest.fixture()
def override_current_user():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id="U_TEST_USER"
    )
    yield
    app.dependency_overrides.clear()


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
    fake_reminder = MedicationReminder(
        creator_user_id="U_TEST_USER",
        user_id="U_TEST_USER",
        slot_type="evening",
        scheduled_time="18:00",
    )
    with patch(
        "app.services.medication.medication_service.MedicationService.get_user_reminders",
        new_callable=AsyncMock,
        return_value=[fake_reminder],
    ):
        response = client.get("/api/medications/reminders")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["slot_type"] == "evening"


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
