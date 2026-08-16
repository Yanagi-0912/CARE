import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import load_dotenv

load_dotenv()


def fake_line_token_manager(token: str = "test-token", side_effect=None) -> MagicMock:
    """`LineTokenManager` 的測試替身，同步與非同步兩支都配好。

    `LineTokenManager` 有兩個取得 token 的入口，生產程式碼依所在情境擇一：

    - `get_token()`      同步，會阻塞呼叫它的執行緒。只剩同步路徑在用
                         （`mutimedia_processor` 的 helper，本身跑在工作執行緒裡）。
    - `get_token_async()` 非同步，快取命中直接回傳、需要刷新才切到工作執行緒。
                         所有 `async def` 裡的呼叫端都用這支。

    只配其中一支的替身，會讓另一條路徑拿到 MagicMock 而不是 token——而且
    `await MagicMock()` 會拋 TypeError，在有吞例外的呼叫端（例如
    `LineLoadingAnimationService.start`）會表現成「什麼都沒發生」，很難查。
    這個工廠把「兩支都要設」這件事收在一處，呼叫端不必各自記得。
    """
    manager = MagicMock()
    if side_effect is not None:
        manager.get_token.side_effect = side_effect
        manager.get_token_async = AsyncMock(side_effect=side_effect)
    else:
        manager.get_token.return_value = token
        manager.get_token_async = AsyncMock(return_value=token)
    return manager


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: 標記需要真實外部服務與 .env 金鑰的整合測試"
    )
    

def pytest_collection_modifyitems(config, items):

    # 檢查 LINE API 金鑰是否存在於環境變數中
    has_line_credentials = bool(
        os.getenv("LINE_CHANNEL_ID") and os.getenv("LINE_CHANNEL_SECRET")
    )

    # 如果沒有金鑰，就找出所有 integration 測試並跳過它們
    if not has_line_credentials:
        skip_integration = pytest.mark.skip(
            reason="此測試需要 LINE_CHANNEL_ID 和 LINE_CHANNEL_SECRET 環境變數"
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)
