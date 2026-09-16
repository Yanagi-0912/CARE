"""路由層測試共用的前置。"""

import pytest

from app.dependencies import reset_rate_limits


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """每個測試從零開始計數。

    頻率限制是行程內的計數器：同一個假使用者在整個測試行程裡會打上百次
    prescription-scan，不重置的話會在某個毫不相干的測試裡撞到 429。
    """
    reset_rate_limits()
    yield
    reset_rate_limits()
