"""lifespan 的索引建立：失敗只記 log，不讓兩個 pod 一起 CrashLoop。

以原始碼比對而非啟動整個 app（理由同 test_family_rbac_metrics_repository）：
lifespan 會連資料庫、建索引、載入院所名稱索引並組裝排程器，在單元測試裡跑不動。
"""

import inspect
import logging
import re

import pytest

from app import main


async def test_ensure_indexes_or_log_swallows_failure_and_logs(caplog):
    async def _boom():
        raise RuntimeError("E11000 duplicate key")

    with caplog.at_level(logging.ERROR):
        await main.ensure_indexes_or_log("users", _boom)

    assert "users 的索引建立失敗" in caplog.text
    assert "duplicate key" in caplog.text


async def test_ensure_indexes_or_log_awaits_the_builder():
    calls = []

    async def _ok():
        calls.append(True)

    await main.ensure_indexes_or_log("x", _ok)

    assert calls == [True]


@pytest.mark.parametrize(
    "repo",
    [
        "UserProfileRepository",
        "ConsultationRepository",
        "ConversationLogRepository",
        "KnowledgeReportRepository",
        "KnowledgeReportPreviewRepository",
    ],
)
def test_startup_wraps_these_index_builders(repo):
    """這幾支 `ensure_indexes` 不吞例外，直接 await 會讓啟動失敗。
    KnowledgeReportPreviewRepository 以前根本沒被呼叫，TTL 從未生效。"""
    source = inspect.getsource(main.lifespan)

    assert re.search(
        rf"ensure_indexes_or_log\(\s*\"[a-z_]+\",\s*{repo}\.ensure_indexes\s*\)", source
    ), f"{repo}.ensure_indexes 沒有經過 ensure_indexes_or_log"
    assert f"await {repo}.ensure_indexes()" not in source
