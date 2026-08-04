from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException

from app.models.family_tree import FamilyMember, FamilyTree
from app.models.medication import (
    CreateMedicationReminderRequest,
    MedicationLog,
    MedicationReminder,
    UpdateMedicationReminderRequest,
)
from app.services.medication.medication_service import MedicationService


@pytest.fixture()
def medication_service():
    return MedicationService()


@pytest.mark.asyncio
async def test_create_reminders_for_self(medication_service):
    req = CreateMedicationReminderRequest(
        user_id="U_SELF",
        slots=["morning", "evening"],
        start_date="2026-07-26",
    )
    with patch(
        "app.services.medication.medication_service.MedicationReminderRepository.create_reminder",
        new_callable=AsyncMock,
    ) as mock_create:
        mock_create.side_effect = lambda r: r
        reminders = await medication_service.create_reminders(
            creator_user_id="U_SELF", request=req
        )

        assert len(reminders) == 2
        assert reminders[0].slot_type == "morning"
        assert reminders[0].scheduled_time == "08:00"
        assert reminders[1].slot_type == "evening"
        assert reminders[1].scheduled_time == "18:00"


@pytest.mark.asyncio
async def test_create_reminders_for_family_member(medication_service):
    fake_tree = FamilyTree(
        user_id="U_CARE",
        family_members=[FamilyMember(user_id="U_MEMBER", is_care_recipient=True)],
        created_at="2026-07-26T00:00:00Z",
        updated_at="2026-07-26T00:00:00Z",
    )
    req = CreateMedicationReminderRequest(
        user_id="U_MEMBER",
        slots=["noon"],
        start_date="2026-07-26",
    )
    with patch(
        "app.services.medication.medication_service.FamilyTreeRepository.get_by_user_id",
        new_callable=AsyncMock,
        return_value=fake_tree,
    ), patch(
        "app.services.medication.medication_service.MedicationReminderRepository.create_reminder",
        new_callable=AsyncMock,
        side_effect=lambda r: r,
    ):
        reminders = await medication_service.create_reminders(
            creator_user_id="U_CARE", request=req
        )
        assert len(reminders) == 1
        assert reminders[0].user_id == "U_MEMBER"
        assert reminders[0].slot_type == "noon"


@pytest.mark.asyncio
async def test_create_reminders_rejects_non_family_member(medication_service):
    fake_tree = FamilyTree(
        user_id="U_CARE",
        family_members=[],
        created_at="2026-07-26T00:00:00Z",
        updated_at="2026-07-26T00:00:00Z",
    )
    req = CreateMedicationReminderRequest(
        user_id="U_STRANGER",
        slots=["morning"],
    )
    with patch(
        "app.services.medication.medication_service.FamilyTreeRepository.get_by_user_id",
        new_callable=AsyncMock,
        return_value=fake_tree,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await medication_service.create_reminders(
                creator_user_id="U_CARE", request=req
            )
        assert exc_info.value.status_code == 400
        assert "用藥對象必須是您的家庭成員" in exc_info.value.detail


@pytest.mark.asyncio
async def test_confirm_medication_success(medication_service):
    fake_log = MedicationLog(
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-07-26T08:00:00Z",
        timeout_at="2026-07-26T08:30:00Z",
        status="pending",
    )
    taken_log = fake_log.model_copy(update={"status": "taken"})
    with patch(
        "app.services.medication.medication_service.MedicationLogRepository.get_log_by_id",
        new_callable=AsyncMock,
        return_value=fake_log,
    ), patch(
        "app.services.medication.medication_service.MedicationLogRepository.mark_as_taken",
        new_callable=AsyncMock,
        return_value=taken_log,
    ):
        res = await medication_service.confirm_medication(
            log_id="L123", user_id="U_PATIENT"
        )
        assert res.status == "taken"


@pytest.mark.asyncio
async def test_confirm_medication_forbidden_for_other_user(medication_service):
    fake_log = MedicationLog(
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-07-26T08:00:00Z",
        timeout_at="2026-07-26T08:30:00Z",
    )
    with patch(
        "app.services.medication.medication_service.MedicationLogRepository.get_log_by_id",
        new_callable=AsyncMock,
        return_value=fake_log,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await medication_service.confirm_medication(
                log_id="L123", user_id="U_OTHER"
            )
        assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_confirm_medication_missed_status_update(medication_service):
    missed_log = MedicationLog(
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-07-26T08:00:00Z",
        timeout_at="2026-07-26T08:30:00Z",
        status="missed",
    )
    taken_log = missed_log.model_copy(update={"status": "taken"})
    with patch(
        "app.services.medication.medication_service.MedicationLogRepository.get_log_by_id",
        new_callable=AsyncMock,
        return_value=missed_log,
    ), patch(
        "app.services.medication.medication_service.MedicationLogRepository.mark_as_taken",
        new_callable=AsyncMock,
        return_value=taken_log,
    ):
        res = await medication_service.confirm_medication(
            log_id="L123", user_id="U_PATIENT"
        )
        assert res.status == "taken"


@pytest.mark.asyncio
async def test_create_reminders_custom_slot_times(medication_service):
    req = CreateMedicationReminderRequest(
        user_id="U_SELF",
        slots=["morning", "evening"],
        slot_times={"morning": "07:30", "evening": "19:00"},
        start_date="2026-07-29",
    )
    with patch(
        "app.services.medication.medication_service.MedicationReminderRepository.create_reminder",
        new_callable=AsyncMock,
        side_effect=lambda r: r,
    ):
        reminders = await medication_service.create_reminders(
            creator_user_id="U_SELF", request=req
        )

        assert len(reminders) == 2
        assert reminders[0].scheduled_time == "07:30"
        assert reminders[1].scheduled_time == "19:00"


@pytest.mark.asyncio
async def test_get_user_reminders_family_permission(medication_service):
    fake_tree = FamilyTree(
        user_id="U_CARE",
        family_members=[FamilyMember(user_id="U_MEMBER")],
        created_at="2026-07-26T00:00:00Z",
        updated_at="2026-07-26T00:00:00Z",
    )
    with patch(
        "app.services.medication.medication_service.FamilyTreeRepository.get_by_user_id",
        new_callable=AsyncMock,
        return_value=fake_tree,
    ), patch(
        "app.services.medication.medication_service.MedicationReminderRepository.list_reminders_by_user",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock_list:
        # Allowed for family member
        reminders = await medication_service.get_user_reminders("U_MEMBER", requester_user_id="U_CARE")
        assert reminders == []
        mock_list.assert_awaited_once_with("U_MEMBER")

        # Rejected for non-family member
        with pytest.raises(HTTPException) as exc_info:
            await medication_service.get_user_reminders("U_STRANGER", requester_user_id="U_CARE")
        assert exc_info.value.status_code == 400

