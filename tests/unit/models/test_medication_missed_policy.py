"""用藥逾時通報的通知政策。

與掛號提醒刻意不同：掛號卡片上有要寫入權的按鈕，所以兩種模式都嚴格；這張卡片
只是告知，影子模式下送族譜全員，才不會讓今天收得到通報的建立者突然收不到。
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.family_authorization import (
    STRICT_NOTIFICATION_KINDS,
    notification_recipient_roles,
)
from app.models.family_tree import FamilyMember, FamilyTree
from app.services.family.family_authorization_service import FamilyAuthorizationService

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
ELDER = "U-elder"


def test_goes_to_guardian_and_caregiver():
    assert notification_recipient_roles("medication_missed") == frozenset(
        {"GUARDIAN", "CAREGIVER"}
    )


def test_is_not_strict():
    assert "medication_missed" not in STRICT_NOTIFICATION_KINDS


def _service(state: str) -> FamilyAuthorizationService:
    tree = FamilyTree(
        user_id=ELDER,
        family_members=[
            FamilyMember(user_id="U-guardian", family_role="GUARDIAN"),
            FamilyMember(user_id="U-caregiver", family_role="CAREGIVER"),
            FamilyMember(user_id="U-member", family_role="MEMBER"),
            FamilyMember(user_id="U-unset"),
        ],
        rbac_migration_state=state,
        created_at=NOW,
        updated_at=NOW,
    )
    trees = MagicMock()
    trees.get_by_user_id = AsyncMock(return_value=tree)
    delegations = MagicMock()
    delegations.has_active_delegation = AsyncMock(return_value=False)
    return FamilyAuthorizationService(
        family_tree_repository=trees,
        delegation_repository=delegations,
        enforcement_enabled=True,
    )


@pytest.mark.asyncio
async def test_shadow_family_sends_to_everyone():
    recipients = await _service("shadow").notification_recipients(
        ELDER, "medication_missed"
    )

    assert recipients == ["U-guardian", "U-caregiver", "U-member", "U-unset"]


@pytest.mark.asyncio
async def test_enforced_family_sends_to_those_who_manage_medication():
    recipients = await _service("enforced").notification_recipients(
        ELDER, "medication_missed"
    )

    assert recipients == ["U-guardian", "U-caregiver"]
