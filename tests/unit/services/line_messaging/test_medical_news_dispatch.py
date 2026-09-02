"""share_medical_news postback 的路由。

以最小替身直接驗 `_dispatch_postback` 的分支，不建整條 handler 鏈——這個分支
要證明的只有「參數有沒有正確傳到 share service」。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.line_messaging.dispatcher.dispatcher import LineEventDispatcher


class FakeShareService:
    def __init__(self):
        self.calls = []

    async def share(self, *, sharer_id, news_ref, reply_token, language, font_size):
        self.calls.append(
            {
                "sharer_id": sharer_id,
                "news_ref": news_ref,
                "reply_token": reply_token,
                "language": language,
                "font_size": font_size,
            }
        )


def _dispatcher(share_service=None):
    message_handler = MagicMock()
    message_handler._user_profile_service = None
    return LineEventDispatcher(
        message_handler=message_handler,
        media_handler=MagicMock(),
        location_handler=MagicMock(),
        facility_detail_handler=MagicMock(),
        replier=MagicMock(reply=AsyncMock(), reply_flex=AsyncMock()),
        medical_news_share_service=share_service,
    )


def _postback(data: str):
    return SimpleNamespace(
        reply_token="tok",
        postback=SimpleNamespace(data=data),
    )


@pytest.mark.asyncio
async def test_share_medical_news_postback_routes_to_share_service():
    share_service = FakeShareService()
    dispatcher = _dispatcher(share_service)

    await dispatcher._dispatch_postback(
        _postback("action=share_medical_news&news_ref=drug_news:abc"),
        "U1",
        None,
    )

    assert share_service.calls[0]["sharer_id"] == "U1"
    assert share_service.calls[0]["news_ref"] == "drug_news:abc"
    assert share_service.calls[0]["reply_token"] == "tok"


@pytest.mark.asyncio
async def test_share_postback_without_news_ref_is_ignored():
    share_service = FakeShareService()
    dispatcher = _dispatcher(share_service)

    await dispatcher._dispatch_postback(
        _postback("action=share_medical_news"), "U1", None
    )

    assert share_service.calls == []


@pytest.mark.asyncio
async def test_share_postback_tolerates_missing_service():
    """功能沒開時只記 log，不得讓事件處理拋錯。

    與 _medication_service 為 None 時的既有處理一致。
    """
    dispatcher = _dispatcher(None)

    await dispatcher._dispatch_postback(
        _postback("action=share_medical_news&news_ref=drug_news:abc"), "U1", None
    )
