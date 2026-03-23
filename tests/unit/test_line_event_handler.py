import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from linebot.v3.webhooks import (
    MessageEvent,
    FileMessageContent,
    ImageMessageContent,
)
from app.services.line.event_handler import LineEventContext

@pytest.fixture
def mock_event():
    event = MagicMock(spec=MessageEvent)
    event.reply_token = "dummy_token"
    # Mock user source
    source = MagicMock()
    source.user_id = "U12345"
    event.source = source
    return event

@pytest.mark.asyncio
async def test_handle_media_message_infers_image(mock_event):
    # 模擬接收到的 File 檔案，副檔名為圖片
    message = MagicMock(spec=FileMessageContent)
    message.id = "M123"
    message.type = "file"
    message.file_name = "test.png"
    mock_event.message = message

    context = LineEventContext(mock_event)
    
    # 攔截 _process_media_and_reply，驗證傳遞進去的參數
    with patch.object(context, "_process_media_and_reply", new_callable=AsyncMock) as mock_process:
        await context.handle_media_message()

        # 斷言：應該被判斷為 "image" 而不是 "file"
        mock_process.assert_called_once_with(
            media_message_id="M123",
            user_media_type="image",
            source_file_name="test.png",
        )

@pytest.mark.asyncio
async def test_handle_media_message_infers_video(mock_event):
    # 模擬接收到的 File 檔案，副檔名為影片
    message = MagicMock(spec=FileMessageContent)
    message.id = "M124"
    message.type = "file"
    message.file_name = "demo.mp4"
    mock_event.message = message

    context = LineEventContext(mock_event)
    
    with patch.object(context, "_process_media_and_reply", new_callable=AsyncMock) as mock_process:
        await context.handle_media_message()

        mock_process.assert_called_once_with(
            media_message_id="M124",
            user_media_type="video",
            source_file_name="demo.mp4",
        )

@pytest.mark.asyncio
async def test_handle_media_message_infers_audio(mock_event):
    # 模擬接收到的 File 檔案，副檔名為音訊
    message = MagicMock(spec=FileMessageContent)
    message.id = "M125"
    message.type = "file"
    message.file_name = "voice.mp3"
    mock_event.message = message

    context = LineEventContext(mock_event)
    
    with patch.object(context, "_process_media_and_reply", new_callable=AsyncMock) as mock_process:
        await context.handle_media_message()

        mock_process.assert_called_once_with(
            media_message_id="M125",
            user_media_type="audio",
            source_file_name="voice.mp3",
        )

@pytest.mark.asyncio
async def test_handle_media_message_unknown_file(mock_event):
    # 模擬接收到的 File 檔案，副檔名不被認識 (如 pdf)
    message = MagicMock(spec=FileMessageContent)
    message.id = "M126"
    message.type = "file"
    message.file_name = "document.pdf"
    mock_event.message = message

    context = LineEventContext(mock_event)
    
    with patch.object(context, "_process_media_and_reply", new_callable=AsyncMock) as mock_process:
        await context.handle_media_message()

        # PDF 不在自動轉換清單內，應維持原本的 "file"
        mock_process.assert_called_once_with(
            media_message_id="M126",
            user_media_type="file",
            source_file_name="document.pdf",
        )

@pytest.mark.asyncio
async def test_handle_media_message_native_image(mock_event):
    # 測試原生就是直接傳送圖片 (ImageMessage)
    message = MagicMock(spec=ImageMessageContent)
    message.id = "M127"
    message.type = "image"
    # 原生圖片物件在 LINE API 通常沒有 file_name 屬性 (所以 getattr 應該取空)
    del message.file_name 
    mock_event.message = message

    context = LineEventContext(mock_event)
    
    with patch.object(context, "_process_media_and_reply", new_callable=AsyncMock) as mock_process:
        await context.handle_media_message()

        # 應該照常傳入 image，且 source_file_name=None
        mock_process.assert_called_once_with(
            media_message_id="M127",
            user_media_type="image",
            source_file_name=None,
        )
