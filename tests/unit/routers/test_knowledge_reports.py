from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_knowledge_report_service,
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
def mock_service():
    service = MagicMock()
    service.create = AsyncMock(return_value=_sample_report())
    service.list_for_user = AsyncMock(return_value=[_sample_report()])
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


def test_admin_approve_requires_key(mock_service):
    with patch("app.dependencies.settings.KNOWLEDGE_REPORTS_ADMIN_API_KEY", "secret-key"):
        response = client.post(
            "/api/admin/knowledge-reports/KR-20260802-AB12/approve",
            json={"selected_urls": [ALLOWED_URL]},
        )
        assert response.status_code == 401

        response = client.post(
            "/api/admin/knowledge-reports/KR-20260802-AB12/approve",
            json={"selected_urls": [ALLOWED_URL]},
            headers={"X-Admin-Key": "secret-key"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "resolved"


def test_admin_reject(mock_service):
    with patch("app.dependencies.settings.KNOWLEDGE_REPORTS_ADMIN_API_KEY", "secret-key"):
        response = client.post(
            "/api/admin/knowledge-reports/KR-20260802-AB12/reject",
            json={"reviewer_note": "no"},
            headers={"X-Admin-Key": "secret-key"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"


def test_admin_key_not_configured_returns_503(mock_service):
    with patch("app.dependencies.settings.KNOWLEDGE_REPORTS_ADMIN_API_KEY", ""):
        response = client.post(
            "/api/admin/knowledge-reports/KR-20260802-AB12/reject",
            json={},
            headers={"X-Admin-Key": "anything"},
        )
        assert response.status_code == 503
