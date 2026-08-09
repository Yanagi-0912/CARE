from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_knowledge_report_service,
    get_user_profile_service,
    require_admin_user,
)
from app.main import app
from app.models.knowledge_report import IngestJob, KnowledgeReport

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
    service.list_for_admin = AsyncMock(
        return_value=([_sample_report()], 1, {"pending": 1, "reviewing": 0})
    )
    service.approve = AsyncMock(
        return_value=_sample_report(
            status="reviewing",
            ingest_job=IngestJob(
                selected_urls=[ALLOWED_URL],
                status="running",
                started_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
            ),
        )
    )
    service.run_ingest = AsyncMock(return_value=None)
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
    assert data["total"] == 1
    assert data["limit"] == 50
    assert data["offset"] == 0
    assert data["status_counts"] == {"pending": 1, "reviewing": 0}
    mock_service.list_for_admin.assert_awaited_once_with(
        status=None, limit=50, offset=0
    )


def test_admin_list_with_status_filter(mock_service, override_admin_user):
    response = client.get("/api/admin/knowledge-reports?status=pending")
    assert response.status_code == 200
    mock_service.list_for_admin.assert_awaited_once_with(
        status="pending", limit=50, offset=0
    )


def test_admin_list_rejects_invalid_status(mock_service, override_admin_user):
    response = client.get("/api/admin/knowledge-reports?status=foo")
    assert response.status_code == 422
    mock_service.list_for_admin.assert_not_awaited()


def test_admin_list_with_pagination(mock_service, override_admin_user):
    response = client.get("/api/admin/knowledge-reports?limit=20&offset=20")
    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 20
    assert data["offset"] == 20
    mock_service.list_for_admin.assert_awaited_once_with(
        status=None, limit=20, offset=20
    )


@pytest.mark.parametrize("query", ["limit=500", "limit=0", "offset=-1"])
def test_admin_list_rejects_out_of_range_pagination(
    mock_service, override_admin_user, query
):
    response = client.get(f"/api/admin/knowledge-reports?{query}")
    assert response.status_code == 422
    mock_service.list_for_admin.assert_not_awaited()


def test_admin_approve_success_for_admin(mock_service, override_admin_user):
    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/approve",
        json={"selected_urls": [ALLOWED_URL]},
    )
    assert response.status_code == 200
    data = response.json()
    # ingest 移到背景後，approve 回應只保證登記成功，不再是終局狀態
    assert data["status"] == "reviewing"
    assert data["ingest_job"]["status"] == "running"


def test_admin_approve_schedules_background_ingest(mock_service, override_admin_user):
    # TestClient 會在回應送出後執行 background task
    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/approve",
        json={"selected_urls": [ALLOWED_URL]},
    )
    assert response.status_code == 200
    mock_service.run_ingest.assert_awaited_once_with("KR-20260802-AB12")


def test_admin_approve_400_body_shape(mock_service, override_admin_user):
    """approve 因白名單被拒時，400 body 的 detail 是結構化物件，不是硬編字串。

    fake service 用 app.dependency_overrides 注入（FastAPI 官方機制），
    不是 unittest.mock.patch 改 settings／模組層常數。
    """
    mock_service.approve = AsyncMock(
        side_effect=HTTPException(
            status_code=400,
            detail={
                "code": "url_not_allowed",
                "invalid_urls": [
                    {"url": "https://evil.com/", "reason": "not_allowed"},
                    {"url": "ht!tp://x", "reason": "malformed"},
                ],
                "message": "以下 2 個網址未通過來源白名單，請檢查後重新送出。",
            },
        )
    )

    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/approve",
        json={"selected_urls": ["https://evil.com/", "ht!tp://x"]},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "url_not_allowed"
    assert isinstance(detail["invalid_urls"], list)
    assert len(detail["invalid_urls"]) == 2
    assert detail["invalid_urls"][0] == {
        "url": "https://evil.com/",
        "reason": "not_allowed",
    }


def test_admin_reject_success_for_admin(mock_service, override_admin_user):
    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/reject",
        json={"reviewer_note": "no"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
