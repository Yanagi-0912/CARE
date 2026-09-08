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
    健康網路下實測約 0.7-0.9 秒／條。"""
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
    就永遠掛著。這是潛在缺陷，與是否觀測到無關。"""
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
    """逾時必須遠高於建立連線的成本，否則網路稍差就把「慢」變成「錯」。

    門檻取 5 秒：健康網路下建立連線是 0.7-0.9 秒，5 秒留了約 6 倍餘裕給
    網路品質不佳的開發環境。這個測試原本用 12 秒，那是量測被一條殘留
    路由污染時的假數字（見 app/db/mongo_client.py 的更正紀錄）——門檻本身
    是對的，數字是錯的。
    """
    # 健康網路下建立連線 0.7-0.9 秒；留約 6 倍餘裕。
    min_headroom_ms = 5_000
    assert settings.MONGODB_CONNECT_TIMEOUT_MS > min_headroom_ms
    assert settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS > min_headroom_ms
    assert settings.MONGODB_SOCKET_TIMEOUT_MS > min_headroom_ms


def test_empty_uri_rejected():
    with pytest.raises(ValueError, match="不可為空"):
        get_shared_client("   ")
