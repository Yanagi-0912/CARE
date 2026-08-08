from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_knowledge_report_service,
    get_user_profile_service,
    require_admin_user,
)
from app.main import app
from app.models.knowledge_report import KnowledgeReport

client = TestClient(app)

ALLOWED_URL = "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1"


def _sample_report(**overrides) -> KnowledgeReport:
    now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    data = {
        "report_id": "KR-20260802-AB12",
        "line_user_id": "U_TEST",
        "status": "pending",
        "reason": "missing",
        "question": "高血壓飲食建議？",
        "user_note": None,
        "user_source_urls": [],
        "resolution": None,
        "reviewer_note": None,
        "ingest_job": None,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return KnowledgeReport(**data)


@pytest.fixture
def override_current_user():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id="U_TEST"
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def override_admin_user():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id="U_ADMIN"
    )
    app.dependency_overrides[require_admin_user] = lambda: CurrentUser(
        line_user_id="U_ADMIN"
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(require_admin_user, None)


@pytest.fixture
def mock_service():
    service = MagicMock()
    service.create = AsyncMock(return_value=_sample_report())
    service.list_for_user = AsyncMock(return_value=[_sample_report()])
    service.list_for_admin = AsyncMock(return_value=[_sample_report()])
    service.approve = AsyncMock(return_value=_sample_report(status="resolved"))
    service.reject = AsyncMock(return_value=_sample_report(status="rejected"))
    app.dependency_overrides[get_knowledge_report_service] = lambda: service
    yield service
    app.dependency_overrides.pop(get_knowledge_report_service, None)


def test_create_knowledge_report(override_current_user, mock_service):
    response = client.post(
        "/api/knowledge-reports",
        json={"question": "問題", "reason": "missing"},
    )
    assert response.status_code == 200
    assert response.json()["report_id"] == "KR-20260802-AB12"
    mock_service.create.assert_awaited_once()


def test_list_knowledge_reports(override_current_user, mock_service):
    response = client.get("/api/knowledge-reports")
    assert response.status_code == 200
    data = response.json()
    assert len(data["reports"]) == 1
    assert data["reports"][0]["status"] == "pending"


def test_admin_approve_requires_admin_role(mock_service):
    profile_service = MagicMock()
    profile_service.get_user_profile = AsyncMock(return_value={"role": "user"})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id="U_USER"
    )
    app.dependency_overrides[get_user_profile_service] = lambda: profile_service

    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/approve",
        json={"selected_urls": [ALLOWED_URL]},
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 403

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_user_profile_service, None)


def test_admin_list_requires_admin_role(mock_service):
    profile_service = MagicMock()
    profile_service.get_user_profile = AsyncMock(return_value={"role": "user"})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id="U_USER"
    )
    app.dependency_overrides[get_user_profile_service] = lambda: profile_service

    response = client.get(
        "/api/admin/knowledge-reports",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 403

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_user_profile_service, None)


def test_admin_list_success_for_admin(mock_service, override_admin_user):
    response = client.get("/api/admin/knowledge-reports")
    assert response.status_code == 200
    data = response.json()
    assert len(data["reports"]) == 1
    assert data["reports"][0]["report_id"] == "KR-20260802-AB12"
    mock_service.list_for_admin.assert_awaited_once_with(status=None)


def test_admin_list_with_status_filter(mock_service, override_admin_user):
    response = client.get("/api/admin/knowledge-reports?status=pending")
    assert response.status_code == 200
    mock_service.list_for_admin.assert_awaited_once_with(status="pending")


def test_admin_approve_success_for_admin(mock_service, override_admin_user):
    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/approve",
        json={"selected_urls": [ALLOWED_URL]},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"


def test_admin_reject_success_for_admin(mock_service, override_admin_user):
    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/reject",
        json={"reviewer_note": "no"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
