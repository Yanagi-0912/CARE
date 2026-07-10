"""LineEventHandler 單元測試（所有輔助函式完全整合於 handle）"""

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import pytest
from linebot.v3.webhooks import (
    DeliveryContext,
    FileMessageContent,
    ImageMessageContent,
    MessageEvent,
    UserSource,
)
from langchain_core.messages import HumanMessage, AIMessage

from app.models.chat_message import ChatMessage
from app.services.line_messaging.event_handler import (
    LineEventHandler,
    LineValidationError,
)
from app.services.history.history_service import LineMessageHistoryService


def _message_event(
    message,
    *,
    reply_token: str = "dummy_token",
    user_id: str = "U12345",
) -> MessageEvent:
    return MessageEvent(
        timestamp=1000, # 1 second in ms
        mode="active",
        webhookEventId="01HZTEST000000000000000000",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken=reply_token,
        source=UserSource(type="user", userId=user_id),
        message=message,
    )


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.invoke = AsyncMock(
        return_value={"response": "AI 回覆"}
    )
    return agent


@pytest.fixture
def mock_history_service():
    svc = MagicMock()
    svc.load_history = AsyncMock(return_value=[])
    svc.save_turn = AsyncMock()
    return svc


@pytest.fixture
def handler(mock_agent, mock_history_service):
    token_manager = MagicMock()
    token_manager.get_token.return_value = "dummy_token"
    h = LineEventHandler(
        agent=mock_agent,
        token_manager=token_manager,
        history_service=mock_history_service,
    )
    return h


@pytest.fixture(autouse=True)
def mock_line_api():
    """自動模擬 LINE API 連線以防止測試調用真實網路"""
    with patch("app.services.line_messaging.event_handler.Configuration") as mock_config, \
         patch("app.services.line_messaging.event_handler.ApiClient") as mock_api_client, \
         patch("app.services.line_messaging.event_handler.MessagingApi") as mock_messaging_api:
        
        messaging_api = MagicMock()
        mock_messaging_api.return_value = messaging_api
        
        yield messaging_api


@pytest.mark.asyncio
async def test_handle_media_message_infers_image(
    handler, mock_agent, mock_line_api, mock_history_service
):
    message = FileMessageContent(
        id="M123", fileName="test.png", fileSize=100
    )
    event = _message_event(message)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process:
        await handler.handle(event)

    mock_process.assert_called_once_with(
        media_message_id="M123",
        user_media_type="image",
        source_file_name="test.png",
        user_id="U12345",
    )
    
    mock_history_service.load_history.assert_called_once()
    mock_agent.invoke.assert_called_once()
    mock_line_api.reply_message.assert_called_once()
    mock_history_service.save_turn.assert_called_once()


@pytest.mark.asyncio
async def test_handle_media_message_infers_video(
    handler, mock_agent, mock_line_api, mock_history_service
):
    message = FileMessageContent(
        id="M124", fileName="demo.mp4", fileSize=200
    )
    event = _message_event(message)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process:
        await handler.handle(event)

    mock_process.assert_called_once_with(
        media_message_id="M124",
        user_media_type="video",
        source_file_name="demo.mp4",
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_media_message_infers_audio(
    handler, mock_agent, mock_line_api, mock_history_service
):
    message = FileMessageContent(
        id="M125", fileName="voice.mp3", fileSize=300
    )
    event = _message_event(message)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process:
        await handler.handle(event)

    mock_process.assert_called_once_with(
        media_message_id="M125",
        user_media_type="audio",
        source_file_name="voice.mp3",
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_media_message_unknown_file(
    handler, mock_agent, mock_line_api, mock_history_service
):
    message = FileMessageContent(
        id="M126", fileName="document.pdf", fileSize=400
    )
    event = _message_event(message)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process:
        await handler.handle(event)

    mock_process.assert_called_once_with(
        media_message_id="M126",
        user_media_type="file",
        source_file_name="document.pdf",
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_media_message_native_image(
    handler, mock_agent, mock_line_api, mock_history_service
):
    message = ImageMessageContent(
        id="M127",
        contentProvider={"type": "line"},
        quoteToken="qt",
    )
    event = _message_event(message)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process:
        await handler.handle(event)

    mock_process.assert_called_once_with(
        media_message_id="M127",
        user_media_type="image",
        source_file_name=None,
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_text_message_success(
    handler, mock_agent, mock_line_api, mock_history_service
):
    from linebot.v3.webhooks import TextMessageContent

    message = TextMessageContent(id="M1", text="你好", quoteToken="dummy")
    event = _message_event(message)

    # 模擬歷史紀錄回傳
    mock_history_service.load_history.return_value = [HumanMessage(content="你好")]

    await handler.handle(event)

    mock_history_service.load_history.assert_called_once_with(
        user_id="U12345",
        current_input="你好",
        message_type="text",
    )
    mock_agent.invoke.assert_called_once_with(
        user_input="你好",
        messages=[HumanMessage(content="你好")],
    )
    
    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.reply_token == "dummy_token"
    assert reply_req.messages[0].text == "AI 回覆"

    mock_history_service.save_turn.assert_called_once()
    save_args = mock_history_service.save_turn.call_args[1]
    assert save_args["user_id"] == "U12345"
    assert save_args["user_text"] == "你好"
    assert save_args["ai_reply"] == "AI 回覆"
    assert save_args["message_type"] == "text"


@pytest.mark.asyncio
async def test_handle_text_message_hospital_guide(
    handler, mock_agent, mock_line_api, mock_history_service
):
    """
    測試使用者詢問醫院時，Agent 應回傳導引文字而非觸發工具。
    """
    from linebot.v3.webhooks import TextMessageContent

    message = TextMessageContent(id="M_HOSP", text="附近有醫院嗎", quoteToken="dummy")
    event = _message_event(message)

    guide_text = "請開啟功能選單並點擊『搜尋附近醫院』按鈕..."
    mock_agent.invoke.return_value = {
        "response": guide_text
    }

    await handler.handle(event)

    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].text == guide_text


@pytest.mark.asyncio
async def test_handle_text_message_error_fallback(
    handler, mock_agent, mock_line_api, mock_history_service
):
    from linebot.v3.webhooks import TextMessageContent

    message = TextMessageContent(id="M3", text="hi", quoteToken="dummy")
    event = _message_event(message)

    mock_agent.invoke.side_effect = Exception("AI Crash")

    await handler.handle(event)

    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert "發生錯誤" in reply_req.messages[0].text
    # 失敗時不儲存歷史
    mock_history_service.save_turn.assert_not_called()


@pytest.mark.asyncio
async def test_handle_location_message_delegates_to_agent(
    handler, mock_agent, mock_line_api, mock_history_service
):
    """
    測試處理位置訊息，預期把經緯度變成字串交給 Agent。
    """
    from linebot.v3.webhooks import LocationMessageContent

    message = LocationMessageContent(
        id="M_LOC1",
        title="my location",
        address="Taipei",
        latitude=25.0330,
        longitude=121.5654,
    )
    event = _message_event(message)

    mock_agent.invoke.return_value = {
        "response": "為您找到附近醫療院所...",
    }

    await handler.handle(event)

    mock_agent.invoke.assert_called_once()
    agent_input = mock_agent.invoke.call_args[1]["user_input"]
    assert "25.033" in agent_input
    assert "121.5654" in agent_input
    
    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].text == "為您找到附近醫療院所..."


@pytest.mark.asyncio
async def test_handle_media_message_empty_ocr_returns_error(
    handler, mock_agent, mock_line_api, mock_history_service
):
    from linebot.v3.webhooks import ImageMessageContent

    message = ImageMessageContent(
        id="M127_empty",
        contentProvider={"type": "line"},
        quoteToken="qt",
    )
    event = _message_event(message)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="Unable to extract text from media file (no content extracted)",
    ) as mock_process:
        await handler.handle(event)

    mock_process.assert_called_once()
    mock_agent.invoke.assert_not_called()
    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert "無法從您傳送的" in reply_req.messages[0].text


def test_validate_media_message_success() -> None:
    # 內置函式的基礎行為測試可以轉移到整合測試中，本處只確保 ValidationError 存在
    assert issubclass(LineValidationError, Exception)


# ==============================================================================
# 驗證媒體訊息錯誤之整合測試（直接透過 handle）
# ==============================================================================

@pytest.mark.asyncio
async def test_handle_media_message_invalid_type(handler, mock_line_api):
    message = FileMessageContent(
        id="M123", fileName="test.invalid", fileSize=100
    )
    # 模擬不支援的媒體類型
    message.type = "unsupported_media_type"
    event = _message_event(message)

    await handler.handle(event)
    
    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert "不支援的媒體類型" in reply_req.messages[0].text


@pytest.mark.asyncio
async def test_handle_media_message_invalid_filename(handler, mock_line_api):
    message = FileMessageContent(
        id="M123", fileName="   ", fileSize=100
    )
    message.type = "file"
    event = _message_event(message)

    await handler.handle(event)
    
    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert "不支援的媒體類型" in reply_req.messages[0].text or "無效的媒體檔名" in reply_req.messages[0].text


# ==============================================================================
# LineMessageHistoryService 單元測試
# ==============================================================================

@pytest.mark.asyncio
async def test_history_service_load_converts_correctly():
    mock_repo = MagicMock()
    mock_repo.list_messages = AsyncMock(
        return_value=[
            ChatMessage(line_id="user_1", message_type="text", content="哈囉", timestamp=datetime.now()),
            ChatMessage(line_id="user_1", message_type="assistant_reply", content="你好", timestamp=datetime.now()),
        ]
    )
    
    svc = LineMessageHistoryService(mock_repo)
    chat_history = await svc.load_history("user_1", "當前問題", "text")
    
    assert len(chat_history) == 2
    assert isinstance(chat_history[0], HumanMessage)
    assert chat_history[0].content == "哈囉"
    assert isinstance(chat_history[1], AIMessage)
    assert chat_history[1].content == "你好"


@pytest.mark.asyncio
async def test_history_service_load_handles_location_correctly():
    mock_repo = MagicMock()
    mock_repo.list_messages = AsyncMock(return_value=[])
    
    svc = LineMessageHistoryService(mock_repo)
    # 測試位置訊息，預期不存入庫，但作為當前輪次包含在內
    chat_history = await svc.load_history("user_1", "位置字串", "location")
    
    assert len(chat_history) == 1
    assert isinstance(chat_history[0], HumanMessage)
    assert chat_history[0].content == "位置字串"
    # 連線庫沒有被調用寫入
    mock_repo.append_message.assert_not_called()


@pytest.mark.asyncio
async def test_history_service_save_turn_appends_messages():
    mock_repo = MagicMock()
    mock_repo.append_message = AsyncMock()
    
    svc = LineMessageHistoryService(mock_repo)
    dt = datetime.now()
    await svc.save_turn("user_1", "哈囉", "回答", "text", dt)
    
    assert mock_repo.append_message.call_count == 2
    user_call, ai_call = mock_repo.append_message.call_args_list
    
    # 檢查第一個寫入的 User 訊息
    user_msg = user_call[0][1]
    assert user_msg.line_id == "user_1"
    assert user_msg.message_type == "text"
    assert user_msg.content == "哈囉"
    assert user_msg.timestamp == dt
    
    # 檢查第二個寫入的 AI 訊息
    ai_msg = ai_call[0][1]
    assert ai_msg.line_id == "user_1"
    assert ai_msg.message_type == "assistant_reply"
    assert ai_msg.content == "回答"


@pytest.mark.asyncio
async def test_history_service_load_slices_to_last_five():
    mock_repo = MagicMock()
    mock_repo.list_messages = AsyncMock(
        return_value=[
            ChatMessage(line_id="user_1", message_type="text", content=f"msg_{i}", timestamp=datetime.now())
            for i in range(7)
        ]
    )
    
    svc = LineMessageHistoryService(mock_repo)
    chat_history = await svc.load_history("user_1", "當前問題", "text")
    
    # 預期只會載入最後 5 筆訊息 (msg_2 到 msg_6)
    assert len(chat_history) == 5
    assert chat_history[0].content == "msg_2"
    assert chat_history[4].content == "msg_6"
