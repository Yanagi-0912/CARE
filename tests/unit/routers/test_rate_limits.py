"""四支端點的頻率限制真的接上了嗎——以 HTTP 直接驗證。

限制值取自 settings；這裡不重複驗計數邏輯（tests/unit/core/test_rate_limit.py
已窮舉），只驗「第 N+1 次是 429、帶 Retry-After、且擋在業務邏輯之前」，
以及計數鍵的方向（IP 對 IP、使用者對使用者）。
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.rate_limit import RateLimiter
from app.dependencies import (
    CurrentUser,
    get_consultation_service,
    get_current_user,
    get_family_tree_service,
    get_liff_auth_application_service,
    get_prescription_scan_service,
    limit_by_user,
    prescription_scan_rate_limit,
    require_prescription_scan_enabled,
)
from app.main import app


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    app.dependency_overrides.clear()


def _login_as(user_id: str):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(line_user_id=user_id)


# ── POST /api/auth/liff/login（IP）────────────────────────────────────


def test_liff_login_is_limited_per_ip(client):
    service = AsyncMock()
    service.login_with_id_token.return_value = {
        "access_token": "t",
        "token_type": "Bearer",
        "expires_in": 60,
        "line_user_id": "U1",
        "language": "zh-TW",
    }
    app.dependency_overrides[get_liff_auth_application_service] = lambda: service
    limit = settings.RATE_LIMIT_LIFF_LOGIN_PER_MINUTE

    for _ in range(limit):
        assert client.post("/api/auth/liff/login", json={"id_token": "x"}).status_code == 200
    blocked = client.post("/api/auth/liff/login", json={"id_token": "x"})

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert service.login_with_id_token.await_count == limit  # 擋在業務邏輯之前

    # 另一個 IP 不受影響
    other = client.post(
        "/api/auth/liff/login",
        json={"id_token": "x"},
        headers={"CF-Connecting-IP": "203.0.113.7"},
    )
    assert other.status_code == 200


# ── GET /api/family/invites/verify/{code}（IP）───────────────────────


def test_invite_verify_is_limited_per_ip(client):
    from datetime import datetime, timezone

    from app.models.family_tree import PendingInvitation

    service = AsyncMock()
    service.verify_invitation.return_value = PendingInvitation(
        _id="code",
        inviter_id="U1",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    app.dependency_overrides[get_family_tree_service] = lambda: service
    limit = settings.RATE_LIMIT_INVITE_VERIFY_PER_MINUTE

    for _ in range(limit):
        assert client.get("/api/family/invites/verify/code").status_code == 200
    assert client.get("/api/family/invites/verify/code").status_code == 429
    assert service.verify_invitation.await_count == limit


# ── POST /api/consultations/me/summary/generate（使用者）──────────────


def test_summary_generate_is_limited_per_user(client):
    from datetime import date, datetime, timezone

    from app.models.consultation import ConsultationSummary

    consultations = AsyncMock()
    consultations.summarize.return_value = ConsultationSummary(
        line_id="U1",
        summary_date=date(2026, 9, 16),
        summary="ok",
        language="zh-TW",
        created_at=datetime.now(timezone.utc),
    )
    app.dependency_overrides[get_consultation_service] = lambda: consultations
    _login_as("U1")
    limit = settings.RATE_LIMIT_SUMMARY_GENERATE_PER_HOUR

    for _ in range(limit):
        assert client.post("/api/consultations/me/summary/generate", json={}).status_code == 200
    assert client.post("/api/consultations/me/summary/generate", json={}).status_code == 429
    assert consultations.summarize.await_count == limit

    # 換一個使用者，同一個 IP，不受影響：鍵是使用者不是 IP
    _login_as("U2")
    assert client.post("/api/consultations/me/summary/generate", json={}).status_code == 200


def test_unauthenticated_requests_get_401_not_counted(client):
    """沒帶 token 先拿 401，不進計數——未登入的濫用另有 IP 層的限制。"""
    assert client.post("/api/consultations/me/summary/generate", json={}).status_code == 401


# ── POST /api/medications/prescription-scan（使用者）─────────────────


def test_prescription_scan_is_limited_per_user(client):
    """用一個上限 1 的替身，不必真的上傳十張圖。"""
    app.dependency_overrides[require_prescription_scan_enabled] = lambda: None
    app.dependency_overrides[prescription_scan_rate_limit] = limit_by_user(
        RateLimiter(limit=1, window_seconds=3600)
    )
    scan_service = AsyncMock()
    app.dependency_overrides[get_prescription_scan_service] = lambda: scan_service
    _login_as("U1")

    # 內容型別不對會 415——但那是在限頻**之後**，所以第二次仍然是 429
    files = {"file": ("x.txt", b"not-an-image", "text/plain")}
    assert client.post("/api/medications/prescription-scan", files=files).status_code == 415
    assert client.post("/api/medications/prescription-scan", files=files).status_code == 429


def test_prescription_scan_feature_flag_wins_over_rate_limit(client):
    """功能關閉時一律 404，不能先回 429 洩漏「功能存在」。"""
    from app.dependencies import get_prescription_scan_enabled

    app.dependency_overrides[get_prescription_scan_enabled] = lambda: False
    app.dependency_overrides[prescription_scan_rate_limit] = limit_by_user(
        RateLimiter(limit=1, window_seconds=3600)
    )
    _login_as("U1")
    files = {"file": ("x.txt", b"x", "text/plain")}
    for _ in range(3):
        assert client.post("/api/medications/prescription-scan", files=files).status_code == 404
