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
