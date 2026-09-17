"""share_care 工具：AI 判斷使用者想分享 CARE 或邀請家人時，叫出同一張分享卡。"""

import json

import pytest

from app.i18n.messages import t
from app.services.line_messaging.share_card import ShareCardService
from app.tools import share_tools
from app.tools.registry import get_all_tools

LIFF_URL = "https://liff.line.me/2009177739-JJrPzjAn"


class FakeOfficialAccount:
    def __init__(self, basic_id):
        self._basic_id = basic_id

    async def get_basic_id(self):
        return self._basic_id


@pytest.fixture(autouse=True)
def reset_tool_state():
    share_tools.configure_share_tool(None)
    yield
    share_tools.configure_share_tool(None)


async def test_service_returns_the_share_card_json():
    service = ShareCardService(FakeOfficialAccount("@460xmyhp"), liff_url=LIFF_URL)

    payload = json.loads(await service.build_reply_text())

    assert payload["type"] == "flex"
    assert "https://line.me/R/ti/p/%40460xmyhp" in payload["followUpText"]


async def test_service_says_unavailable_without_a_basic_id():
    """拿不到官方帳號 ID 就不送卡片：少了 ID 的連結與 QR 全是壞的。"""
    service = ShareCardService(FakeOfficialAccount(None), liff_url=LIFF_URL)

    assert await service.build_reply_text() == t("share.unavailable")


async def test_tool_returns_the_card_from_the_configured_service():
    share_tools.configure_share_tool(
        ShareCardService(FakeOfficialAccount("@460xmyhp"), liff_url=LIFF_URL)
    )

    result = await share_tools.share_care.ainvoke({})

    assert json.loads(result)["type"] == "flex"


async def test_tool_says_unavailable_when_not_configured():
    assert await share_tools.share_care.ainvoke({}) == t("share.unavailable")


def test_share_care_is_offered_whether_or_not_rag_is_allowed():
    """分享卡跟知識庫無關，不隨 guardrail 的 include_rag_tool 開關。"""
    for include_rag in (True, False):
        names = {
            getattr(tool, "name", str(tool))
            for tool in get_all_tools(include_rag_tool=include_rag)
        }
        assert "share_care" in names
