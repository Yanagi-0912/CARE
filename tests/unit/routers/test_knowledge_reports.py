from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.dependencies import (
    CurrentUser,
    get_content_preview_service,
    get_current_user,
    get_knowledge_report_service,
    get_manual_report_quota,
    get_user_profile_service,
    require_admin_user,
)
from app.main import app
from app.models.knowledge_report import (
    ContentPreview,
    ContentPreviewItem,
    IngestJob,
    KnowledgeReport,
)
from app.services.knowledge_reports.preview_service import PreviewStart

client = TestClient(app)

ALLOWED_URL = "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1"
PREVIEW_CONTENT = "高血壓衛教內容"
PREVIEW_HASH = hashlib.sha256(PREVIEW_CONTENT.encode()).hexdigest()


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
    service.count_manual_reports_since = AsyncMock(return_value=0)
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
    service.get_for_review = AsyncMock(
        return_value=_sample_report(user_source_urls=[ALLOWED_URL])
    )
    app.dependency_overrides[get_knowledge_report_service] = lambda: service
    yield service
    app.dependency_overrides.pop(get_knowledge_report_service, None)


def _sample_preview(**overrides) -> ContentPreview:
    now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    data = {
        "preview_id": "PV-1",
        "report_id": "KR-20260802-AB12",
        "status": "running",
        "items": [
            ContentPreviewItem(
                url=ALLOWED_URL,
                status="ok",
                title="高血壓防治",
                content=PREVIEW_CONTENT,
                content_hash=PREVIEW_HASH,
                char_count=len(PREVIEW_CONTENT),
            )
        ],
        "created_at": now,
        "expires_at": now + timedelta(minutes=60),
    }
    data.update(overrides)
    return ContentPreview(**data)


@pytest.fixture
def mock_preview_service():
    service = MagicMock()
    service.start = AsyncMock(
        return_value=PreviewStart(
            preview=_sample_preview(), urls=[ALLOWED_URL], scheduled=True
        )
    )
    service.run = AsyncMock(return_value=True)
    service.get = AsyncMock(return_value=_sample_preview(status="ready"))
    app.dependency_overrides[get_content_preview_service] = lambda: service
    yield service
    app.dependency_overrides.pop(get_content_preview_service, None)


def _valid_create_body(**overrides) -> dict:
    """手動回報的最小合法請求主體。

    表單只有 URL 與說明兩欄，question 由前端填入說明欄的同一份文字
    （design.md 決策 2），所以這裡兩者相同是刻意的，不是複製貼上失誤。
    """
    body = {
        "question": "這頁高血壓衛教資料已過時",
        "reason": "outdated",
        "user_note": "這頁高血壓衛教資料已過時",
        "user_source_urls": [ALLOWED_URL],
    }
    body.update(overrides)
    return body


def test_create_knowledge_report(override_current_user, mock_service):
    response = client.post("/api/knowledge-reports", json=_valid_create_body())
    assert response.status_code == 200
    assert response.json()["report_id"] == "KR-20260802-AB12"
    mock_service.create.assert_awaited_once()


def test_create_rejects_missing_source_urls(override_current_user, mock_service):
    """user_source_urls 由選填改必填：admin 只看 URL＋說明就要能判斷該不該收。"""
    body = _valid_create_body()
    del body["user_source_urls"]

    response = client.post("/api/knowledge-reports", json=body)

    assert response.status_code == 422
    mock_service.create.assert_not_awaited()


def test_create_rejects_empty_source_urls(override_current_user, mock_service):
    response = client.post(
        "/api/knowledge-reports", json=_valid_create_body(user_source_urls=[])
    )

    assert response.status_code == 422
    mock_service.create.assert_not_awaited()


def test_create_rejects_missing_user_note(override_current_user, mock_service):
    body = _valid_create_body()
    del body["user_note"]

    response = client.post("/api/knowledge-reports", json=body)

    assert response.status_code == 422
    mock_service.create.assert_not_awaited()


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_create_rejects_blank_user_note(override_current_user, mock_service, blank):
    """min_length=1 擋不掉全空白，必須靠 field_validator 先 strip 再判。"""
    response = client.post(
        "/api/knowledge-reports", json=_valid_create_body(user_note=blank)
    )

    assert response.status_code == 422
    mock_service.create.assert_not_awaited()


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_create_rejects_blank_question(override_current_user, mock_service, blank):
    response = client.post(
        "/api/knowledge-reports", json=_valid_create_body(question=blank)
    )

    assert response.status_code == 422
    mock_service.create.assert_not_awaited()


def test_create_rejects_too_many_source_urls(override_current_user, mock_service):
    response = client.post(
        "/api/knowledge-reports",
        json=_valid_create_body(user_source_urls=[ALLOWED_URL] * 4),
    )

    assert response.status_code == 422
    mock_service.create.assert_not_awaited()


def test_create_rejects_overlong_url(override_current_user, mock_service):
    overlong = "https://www.hpa.gov.tw/" + "a" * 2048

    response = client.post(
        "/api/knowledge-reports", json=_valid_create_body(user_source_urls=[overlong])
    )

    assert response.status_code == 422
    mock_service.create.assert_not_awaited()


@pytest.mark.parametrize("field", ["question", "user_note"])
def test_create_rejects_overlong_text(override_current_user, mock_service, field):
    response = client.post(
        "/api/knowledge-reports", json=_valid_create_body(**{field: "字" * 501})
    )

    assert response.status_code == 422
    mock_service.create.assert_not_awaited()


def test_create_marks_report_as_manual(override_current_user, mock_service):
    """來源標記為 manual：配額只算這一種，admin 也才分得出 URL 是誰貼的。"""
    client.post("/api/knowledge-reports", json=_valid_create_body())

    assert mock_service.create.await_args.kwargs["source"] == "manual"


def test_create_passes_question_through_unchanged(override_current_user, mock_service):
    """question 由前端提供，後端不做隱式複製。

    表單把說明欄的文字同時填進 question 與 user_note（design.md 決策 2），
    但複製是前端的事——後端偷偷抄會讓 API 契約多一個沒寫在文件上的行為，
    日後任何新呼叫端都會踩到。
    """
    client.post(
        "/api/knowledge-reports",
        json=_valid_create_body(question="這頁過時了", user_note="這頁過時了"),
    )

    kwargs = mock_service.create.await_args.kwargs
    assert kwargs["question"] == "這頁過時了"
    assert kwargs["user_note"] == "這頁過時了"


def test_create_stores_normalized_urls(override_current_user, mock_service):
    """入庫的是正規化後的網址，不是使用者貼的原字串。

    assert_allowed_urls 回傳正規化結果（補 scheme、去 fragment、剝追蹤參數）。
    若把原字串存進去，白名單正規化的效果就在寫入這一步丟失了。
    """
    client.post(
        "/api/knowledge-reports",
        json=_valid_create_body(
            user_source_urls=["www.hpa.gov.tw/page?utm_source=line#top"]
        ),
    )

    assert mock_service.create.await_args.kwargs["user_source_urls"] == [
        "https://www.hpa.gov.tw/page"
    ]


def test_create_rejects_non_whitelisted_url(override_current_user, mock_service):
    """一次列出全部不合格網址，不是只回第一個。

    使用者貼三個被拒兩個時，只講一個會讓他修完再送、再被拒一次。
    """
    response = client.post(
        "/api/knowledge-reports",
        json=_valid_create_body(
            user_source_urls=["https://www.youtube.com/watch?v=1", "https://evil.com/x"]
        ),
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "url_not_allowed"
    assert [item["url"] for item in detail["invalid_urls"]] == [
        "https://www.youtube.com/watch?v=1",
        "https://evil.com/x",
    ]
    assert {item["reason"] for item in detail["invalid_urls"]} == {"not_allowed"}
    mock_service.create.assert_not_awaited()


def test_create_rejects_backslash_bypass_as_malformed(
    override_current_user, mock_service
):
    """迴歸守門：反斜線繞過。

    Python 的 urlsplit 不把 '\\' 當分隔符，hostname 會是 'evil.com\\.gov.tw'
    而通過裸 endswith 檢查；但 Firecrawl 是 Node 服務，WHATWG 把 '\\' 視同
    '/'，實際抓的是 evil.com。harden-url-whitelist 修掉了它，這裡守住不回退。
    """
    response = client.post(
        "/api/knowledge-reports",
        json=_valid_create_body(user_source_urls=["https://evil.com\\.gov.tw/page"]),
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["invalid_urls"][0]["reason"] == "malformed"
    mock_service.create.assert_not_awaited()


def test_create_reports_mixed_failure_reasons_per_url(
    override_current_user, mock_service
):
    """同一批混合兩種失敗時，逐一標記各自的原因。

    使用者的補救動作不同：malformed 是「重貼一次」，not_allowed 是「這個網站
    我們不收，不是你打錯」。混成同一句會讓貼了正確 youtube 連結的人反覆重試。
    """
    response = client.post(
        "/api/knowledge-reports",
        json=_valid_create_body(
            user_source_urls=["https://evil.com\\.gov.tw/a", "https://www.youtube.com/b"]
        ),
    )

    assert response.status_code == 400
    reasons = {
        item["url"]: item["reason"]
        for item in response.json()["detail"]["invalid_urls"]
    }
    assert reasons == {
        "https://evil.com\\.gov.tw/a": "malformed",
        "https://www.youtube.com/b": "not_allowed",
    }


def test_create_rejects_when_quota_exhausted(override_current_user, mock_service):
    mock_service.count_manual_reports_since = AsyncMock(return_value=2)
    app.dependency_overrides[get_manual_report_quota] = lambda: 2

    response = client.post("/api/knowledge-reports", json=_valid_create_body())

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["code"] == "quota_exceeded"
    assert detail["limit"] == 2
    mock_service.create.assert_not_awaited()

    app.dependency_overrides.pop(get_manual_report_quota, None)


def test_create_allows_when_under_quota(override_current_user, mock_service):
    mock_service.count_manual_reports_since = AsyncMock(return_value=1)
    app.dependency_overrides[get_manual_report_quota] = lambda: 2

    response = client.post("/api/knowledge-reports", json=_valid_create_body())

    assert response.status_code == 200
    mock_service.create.assert_awaited_once()

    app.dependency_overrides.pop(get_manual_report_quota, None)


def test_quota_window_is_rolling_24_hours(override_current_user, mock_service):
    """滾動 24 小時，不是自然日——自然日會讓人在午夜前後送兩倍。"""
    before = datetime.now(timezone.utc)
    client.post("/api/knowledge-reports", json=_valid_create_body())
    after = datetime.now(timezone.utc)

    line_user_id, since = mock_service.count_manual_reports_since.await_args.args
    assert line_user_id == "U_TEST"
    assert before - timedelta(hours=24) <= since <= after - timedelta(hours=24)


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


def _approve_body(**overrides) -> dict:
    """核准的最小合法請求主體：核准的對象是一份具體快照，不是一個網址字串。"""
    body = {
        "selected_urls": [ALLOWED_URL],
        "preview_id": "PV-1",
        "content_hashes": {ALLOWED_URL: PREVIEW_HASH},
    }
    body.update(overrides)
    return body


def test_admin_approve_success_for_admin(mock_service, override_admin_user):
    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/approve",
        json=_approve_body(),
    )
    assert response.status_code == 200
    data = response.json()
    # ingest 移到背景後，approve 回應只保證登記成功，不再是終局狀態
    assert data["status"] == "reviewing"
    assert data["ingest_job"]["status"] == "running"


def test_admin_approve_passes_preview_binding_to_service(
    mock_service, override_admin_user
):
    """綁定參數必須真的傳到 service，否則驗證形同虛設。"""
    client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/approve",
        json=_approve_body(),
    )

    kwargs = mock_service.approve.await_args.kwargs
    assert kwargs["preview_id"] == "PV-1"
    assert kwargs["content_hashes"] == {ALLOWED_URL: PREVIEW_HASH}


def test_admin_approve_schedules_background_ingest(mock_service, override_admin_user):
    # TestClient 會在回應送出後執行 background task
    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/approve",
        json=_approve_body(),
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


def test_admin_preview_returns_202_and_schedules_background_scrape(
    mock_service, mock_preview_service, override_admin_user
):
    """驗證同步完成、抓取排到背景：端點 SHALL NOT 在回應中等外部服務。"""
    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/preview",
        json={"urls": [ALLOWED_URL]},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    mock_preview_service.run.assert_awaited_once_with(
        report_id="KR-20260802-AB12",
        preview_id="PV-1",
        urls=[ALLOWED_URL],
    )


def test_admin_preview_defaults_to_report_source_urls(
    mock_service, mock_preview_service, override_admin_user
):
    client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/preview",
        json={},
    )

    assert mock_preview_service.start.await_args.kwargs["urls"] == [ALLOWED_URL]


def test_admin_preview_does_not_schedule_when_reusing_ready_preview(
    mock_service, mock_preview_service, override_admin_user
):
    """TTL 內沿用既有預覽時不得再排抓取，否則瀏覽佇列就會重複打外部服務。"""
    mock_preview_service.start = AsyncMock(
        return_value=PreviewStart(
            preview=_sample_preview(status="ready"), urls=[ALLOWED_URL], scheduled=False
        )
    )

    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/preview",
        json={"urls": [ALLOWED_URL]},
    )

    assert response.status_code == 202
    mock_preview_service.run.assert_not_awaited()


def test_admin_preview_404_when_report_missing(
    mock_service, mock_preview_service, override_admin_user
):
    mock_service.get_for_review = AsyncMock(
        side_effect=HTTPException(status_code=404, detail="Report not found")
    )

    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/preview",
        json={"urls": [ALLOWED_URL]},
    )

    assert response.status_code == 404
    mock_preview_service.start.assert_not_awaited()


def test_admin_preview_requires_admin_role(mock_service, mock_preview_service):
    profile_service = MagicMock()
    profile_service.get_user_profile = AsyncMock(return_value={"role": "user"})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id="U_USER"
    )
    app.dependency_overrides[get_user_profile_service] = lambda: profile_service

    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/preview",
        json={"urls": [ALLOWED_URL]},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 403
    mock_preview_service.start.assert_not_awaited()

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_user_profile_service, None)


def test_admin_get_preview_returns_items(
    mock_service, mock_preview_service, override_admin_user
):
    response = client.get("/api/admin/knowledge-reports/KR-20260802-AB12/preview")

    assert response.status_code == 200
    data = response.json()
    assert data["preview_id"] == "PV-1"
    item = data["items"][0]
    assert item["url"] == ALLOWED_URL
    assert item["status"] == "ok"
    assert item["content"] == "高血壓衛教內容"
    assert item["content_hash"] == PREVIEW_HASH


def test_admin_get_preview_404_when_absent_or_expired(
    mock_service, mock_preview_service, override_admin_user
):
    mock_preview_service.get = AsyncMock(return_value=None)

    response = client.get("/api/admin/knowledge-reports/KR-20260802-AB12/preview")

    assert response.status_code == 404


def test_admin_get_preview_requires_admin_role(mock_service, mock_preview_service):
    profile_service = MagicMock()
    profile_service.get_user_profile = AsyncMock(return_value={"role": "user"})
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id="U_USER"
    )
    app.dependency_overrides[get_user_profile_service] = lambda: profile_service

    response = client.get(
        "/api/admin/knowledge-reports/KR-20260802-AB12/preview",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 403
    mock_preview_service.get.assert_not_awaited()

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_user_profile_service, None)


def test_admin_reject_success_for_admin(mock_service, override_admin_user):
    response = client.post(
        "/api/admin/knowledge-reports/KR-20260802-AB12/reject",
        json={"reviewer_note": "no"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
