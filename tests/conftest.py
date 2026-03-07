import os
import pytest

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
        skip_integration = pytest.mark.skip(reason="此測試需要 LINE_CHANNEL_ID 和 LINE_CHANNEL_SECRET 環境變數")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)
