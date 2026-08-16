from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from linebot.v3.webhooks import FileMessageContent

from app.services.line_messaging.handler.media_handler import LineMediaHandler


@pytest.fixture
def media_handler(mock_agent, mock_history_service, mock_user_profile_service):
    replier = MagicMock()
    return LineMediaHandler(
        agent=mock_agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=replier,
    )


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.invoke = AsyncMock(return_value={"response": "AI 回覆"})
    return agent


@pytest.fixture
def mock_history_service():
    svc = MagicMock()
    svc.load_history = AsyncMock(return_value=[])
    svc.save_turn = AsyncMock()
    return svc


@pytest.fixture
def mock_user_profile_service():
    svc = MagicMock()
    svc.get_user_profile = AsyncMock(return_value={"voice_reply_enabled": True})
    return svc


@pytest.mark.asyncio
async def test_extract_media_text_file_success_calls_ingest(media_handler):
    message = FileMessageContent(id="M_PDF", fileName="report.pdf", fileSize=100)
    message.type = "file"
    raw_content = "PDF extracted text content"

    mock_ingest = MagicMock()
    mock_ingest.ingest_text = AsyncMock(return_value="doc-id")
    media_handler._user_document_ingest_service = mock_ingest

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value=raw_content,
    ):
        user_text, media_type = await media_handler._extract_media_text(
            message, "U12345"
        )

    mock_ingest.ingest_text.assert_called_once_with(
        "U12345",
        raw_content,
        source_name="report.pdf",
        media_type="file",
    )
    assert user_text == f"以下為使用者傳送的file媒體內容：\n{raw_content}"
    assert media_type == "file"


@pytest.mark.asyncio
async def test_extract_media_text_ingest_failure_still_returns_prefixed_text(
    media_handler,
):
    message = FileMessageContent(id="M_PDF", fileName="report.pdf", fileSize=100)
    message.type = "file"
    raw_content = "PDF extracted text content"

    mock_ingest = MagicMock()
    mock_ingest.ingest_text = AsyncMock(side_effect=RuntimeError("ingest failed"))
    media_handler._user_document_ingest_service = mock_ingest

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value=raw_content,
    ):
        user_text, media_type = await media_handler._extract_media_text(
            message, "U12345"
        )

    mock_ingest.ingest_text.assert_called_once()
    assert user_text == f"以下為使用者傳送的file媒體內容：\n{raw_content}"
    assert media_type == "file"


@pytest.mark.asyncio
async def test_extract_media_text_image_does_not_call_ingest(media_handler):
    message = FileMessageContent(id="M123", fileName="test.png", fileSize=100)

    mock_ingest = MagicMock()
    mock_ingest.ingest_text = AsyncMock()
    media_handler._user_document_ingest_service = mock_ingest

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ):
        await media_handler._extract_media_text(message, "U12345")

    mock_ingest.ingest_text.assert_not_called()


class FakeSafetyAlertService:
    def __init__(self):
        self.calls = []

    async def check(self, user_id, text):
        self.calls.append((user_id, text))


async def _drain(handler):
    import asyncio

    tasks = list(handler._safety_alert_tasks)
    if tasks:
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_ocr_text_reaches_the_safety_check(
    mock_agent, mock_history_service, mock_user_profile_service
):
    """圖片的訊號全部都在既有管線 OCR 出來的文字裡，不需要再處理一次影像。"""
    from datetime import datetime

    from linebot.v3.webhooks import (
        ContentProvider,
        DeliveryContext,
        ImageMessageContent,
        MessageEvent,
        UserSource,
    )

    safety = FakeSafetyAlertService()
    replier = MagicMock()
    replier.reply = AsyncMock(return_value=True)
    handler = LineMediaHandler(
        agent=mock_agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=replier,
        safety_alert_service=safety,
    )
    event = MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZTEST000000000000000001",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId="U12345"),
        message=ImageMessageContent(
            id="M_IMG",
            quoteToken="qt",
            contentProvider=ContentProvider(type="line"),
        ),
    )

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="合利他命強効錠 EX PLUS　アリナミン",
    ):
        await handler.handle(event)
    await _drain(handler)

    assert len(safety.calls) == 1
    assert "合利他命強効錠 EX PLUS" in safety.calls[0][1]


@pytest.mark.asyncio
async def test_media_processor_call_is_unchanged_by_the_safety_check(
    mock_agent, mock_history_service, mock_user_profile_service
):
    """既有媒體路徑的行為 SHALL 與本能力導入前完全相同。"""
    message = FileMessageContent(id="M_PDF", fileName="report.pdf", fileSize=100)
    message.type = "file"
    handler = LineMediaHandler(
        agent=mock_agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=MagicMock(),
        safety_alert_service=FakeSafetyAlertService(),
    )

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="PDF extracted text content",
    ) as mock_process:
        user_text, media_type = await handler._extract_media_text(message, "U12345")

    mock_process.assert_awaited_once_with(
        media_message_id="M_PDF",
        user_media_type="file",
        source_file_name="report.pdf",
        user_id="U12345",
    )
    assert user_text == "以下為使用者傳送的file媒體內容：\nPDF extracted text content"
    assert media_type == "file"


@pytest.mark.asyncio
async def test_failed_media_pipeline_never_reaches_the_safety_check(
    mock_agent, mock_history_service, mock_user_profile_service
):
    """管線回錯誤字串時主回覆是「請重新傳送」，那串字沒有評估的價值。"""
    from datetime import datetime

    from linebot.v3.webhooks import (
        ContentProvider,
        DeliveryContext,
        ImageMessageContent,
        MessageEvent,
        UserSource,
    )

    safety = FakeSafetyAlertService()
    replier = MagicMock()
    replier.reply = AsyncMock(return_value=True)
    handler = LineMediaHandler(
        agent=mock_agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=replier,
        safety_alert_service=safety,
    )
    event = MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZTEST000000000000000002",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId="U12345"),
        message=ImageMessageContent(
            id="M_IMG",
            quoteToken="qt",
            contentProvider=ContentProvider(type="line"),
        ),
    )

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="Unable to extract text from image",
    ):
        with pytest.raises(Exception):
            await handler.handle(event)

    assert safety.calls == []
