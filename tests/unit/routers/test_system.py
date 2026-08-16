from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from app.main import app

client = TestClient(
    app
)  # 開啟假的瀏覽器跟用戶，不用自己開真的瀏覽器測試，測試只會在記憶體跑。


@pytest.mark.parametrize(
    "url, expected_json",
    [
        ("/", {"message": "CARE Backend Running"}),  # 給人看：確認後端有開
        (
            "/health",
            {"status": "Welcome to CARE Backend!"},
        ),  # 給機器看：K8s / Cloud Run 健康檢查
    ],
)
def test_status_endpoints(url, expected_json):
    response = client.get(url)
    assert response.status_code == 200
    assert response.json() == expected_json


def test_favicon_endpoint_returns_no_content():
    response = client.get("/favicon.ico")
    assert response.status_code == 204
    assert response.content == b""


# ── /health/scheduler ────────────────────────────────────────────────
#
# scheduler pod 的 livenessProbe 掛在這支端點上。它與 /health 的差別是後者只
# 證明 uvicorn 還能回應 HTTP——排程器的 asyncio task 整個停掉時 /health 仍然
# 回 200，而用藥提醒已經停止推播且錯過的時段不會補推。


@pytest.fixture
def clean_heartbeat():
    from app.core import scheduler_heartbeat

    scheduler_heartbeat.reset()
    yield scheduler_heartbeat
    scheduler_heartbeat.reset()


def test_scheduler_health_ok_when_beating(clean_heartbeat, monkeypatch):
    monkeypatch.setattr(settings, "APP_ROLE", "scheduler")
    clean_heartbeat.register("medication", expected_interval_seconds=60)

    response = client.get("/health/scheduler")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "medication" in body["schedulers"]


def test_scheduler_health_503_when_stale(clean_heartbeat, monkeypatch):
    """停擺必須讓探針看見，否則提醒靜靜停掉而沒有任何警訊。"""
    monkeypatch.setattr(settings, "APP_ROLE", "scheduler")
    clean_heartbeat.register("medication", expected_interval_seconds=60)
    # 把心跳往回撥，模擬超過 3 倍容忍窗未回報
    clean_heartbeat._last_beat["medication"] = datetime.now(timezone.utc) - timedelta(
        seconds=600
    )

    response = client.get("/health/scheduler")

    assert response.status_code == 503
    assert "medication" in response.json()["detail"]["stale"]


def test_scheduler_health_503_when_nothing_registered(clean_heartbeat, monkeypatch):
    """角色該跑排程器卻一個都沒登記，代表 start() 從未被呼叫。"""
    monkeypatch.setattr(settings, "APP_ROLE", "scheduler")

    response = client.get("/health/scheduler")

    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "no-scheduler-registered"


def test_scheduler_health_ok_for_api_role(clean_heartbeat, monkeypatch):
    """API pod 本來就沒有排程器，不該因此被判成不健康。

    這支端點掛在共用的 app 上，API pod 也看得到。對它回 503 會讓誤設定的
    探針把正常的 API pod 一直重啟。
    """
    monkeypatch.setattr(settings, "APP_ROLE", "api")

    response = client.get("/health/scheduler")

    assert response.status_code == 200
    assert response.json()["status"] == "not-applicable"
