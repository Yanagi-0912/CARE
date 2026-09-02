from unittest.mock import MagicMock, patch

import pytest

# 刻意不 stub motor：本檔在 tests/unit/db/ 會被最早收集，塞假模組進
# sys.modules 會讓後續所有 import 真 motor 的測試一起壞掉（實測 15 個
# 檔案收集失敗）。這裡本來就 patch 掉建構子，不需要假模組。
from app.core.config import settings
from app.db.mongo_client import get_shared_client, reset_shared_clients


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_shared_clients()
    yield
    reset_shared_clients()


def test_same_uri_reuses_one_client():
    """同一個 URI 只建一條連線——重複建立要各自付一次 TLS＋拓撲探索＋認證，
    本機實測那個成本約 12 秒，不是省毫秒的問題。"""
    with patch("app.db.mongo_client.AsyncIOMotorClient") as motor:
        motor.side_effect = lambda *a, **k: MagicMock()
        first = get_shared_client("mongodb://localhost/x")
        second = get_shared_client("mongodb://localhost/x")

    assert first is second
    assert motor.call_count == 1


def test_different_uri_gets_its_own_client():
    """以 URI 為鍵而非全域單例：測試與腳本可能指向不同叢集。"""
    with patch("app.db.mongo_client.AsyncIOMotorClient") as motor:
        motor.side_effect = lambda *a, **k: MagicMock()
        a = get_shared_client("mongodb://localhost/a")
        b = get_shared_client("mongodb://localhost/b")

    assert a is not b
    assert motor.call_count == 2


def test_timeouts_are_applied():
    """socketTimeoutMS 是重點：PyMongo 預設 None＝無限，連上之後對方不回應
    就永遠掛著。實測撞過一次 rag_retrieve 94 秒後回 0 筆。"""
    with patch("app.db.mongo_client.AsyncIOMotorClient") as motor:
        motor.side_effect = lambda *a, **k: MagicMock()
        get_shared_client("mongodb://localhost/x")

    kwargs = motor.call_args.kwargs
    assert kwargs["socketTimeoutMS"] == settings.MONGODB_SOCKET_TIMEOUT_MS
    assert kwargs["connectTimeoutMS"] == settings.MONGODB_CONNECT_TIMEOUT_MS
    assert (
        kwargs["serverSelectionTimeoutMS"]
        == settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS
    )


def test_timeouts_stay_above_observed_cold_start():
    """設得比冷啟動成本低會讓每次重啟後的第一個請求必定失敗——把偶發的慢
    換成穩定的錯。本機實測建立連線約 12 秒，這裡守住那條線。"""
    observed_cold_start_ms = 12_000
    assert settings.MONGODB_CONNECT_TIMEOUT_MS > observed_cold_start_ms
    assert settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS > observed_cold_start_ms
    assert settings.MONGODB_SOCKET_TIMEOUT_MS > observed_cold_start_ms


def test_empty_uri_rejected():
    with pytest.raises(ValueError, match="不可為空"):
        get_shared_client("   ")
