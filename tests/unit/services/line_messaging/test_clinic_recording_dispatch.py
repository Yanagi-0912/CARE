"""看診錄音按鈕（開始、徵詢同意、取消、不是看診）的 postback 路由。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.clinic_transcript.line_flow import postback_data
from app.services.line_messaging.dispatcher.dispatcher import LineEventDispatcher


def _dispatcher(flow):
    message_handler = MagicMock()
    message_handler._user_profile_service = None
    return LineEventDispatcher(
        message_handler=message_handler,
        media_handler=MagicMock(),
        location_handler=MagicMock(),
        facility_detail_handler=MagicMock(),
        replier=MagicMock(reply=AsyncMock()),
        clinic_recording_flow=flow,
    )


def _flow():
    return MagicMock(start=AsyncMock(), choose_consent=AsyncMock(), cancel=AsyncMock())


async def _press(dispatcher, data):
    await dispatcher._dispatch_postback(
        SimpleNamespace(reply_token="tok", postback=SimpleNamespace(data=data)), "U1", None
    )


@pytest.mark.asyncio
async def test_掛號卡片的錄音按鈕帶著掛號資訊開始():
    flow = _flow()
    await _press(
        _dispatcher(flow),
        postback_data("clinic_record_start", appointment_id="a1", hospital_name="臺大醫院"),
    )
    kwargs = flow.start.await_args.kwargs
    assert flow.start.await_args.args[0] == "U1"
    assert kwargs["appointment_id"] == "a1" and kwargs["hospital_name"] == "臺大醫院"


@pytest.mark.asyncio
async def test_選同意方式():
    flow = _flow()
    await _press(_dispatcher(flow), postback_data("clinic_record_consent", mode="self_recap"))
    assert flow.choose_consent.await_args.args[-1] == "self_recap"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action, not_visit", [("clinic_record_cancel", False), ("clinic_record_not_visit", True)]
)
async def test_取消與不是看診(action, not_visit):
    flow = _flow()
    await _press(_dispatcher(flow), postback_data(action))
    assert flow.cancel.await_args.kwargs["not_visit"] is not_visit


@pytest.mark.asyncio
async def test_沒設定流程時不會炸():
    await _press(_dispatcher(None), postback_data("clinic_record_start"))
