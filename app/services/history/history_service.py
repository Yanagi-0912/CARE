import logging
from datetime import datetime, timezone
from langchain_core.messages import HumanMessage, AIMessage, AnyMessage

from app.models.chat_message import ChatMessage

logger = logging.getLogger(__name__)

# agent 每輪只帶最近這麼多則訊息當上下文；Redis 快取也只保留這麼多（見 dependencies）。
RECENT_CONTEXT_MESSAGES = 5


class LineMessageHistoryService:
    """歷史對話記憶服務：Redis 快取供 agent 讀最近幾則，Mongo 是對話原文的正式紀錄。"""

    def __init__(self, chat_history_repository, *, conversation_log):
        self._repo = chat_history_repository
        self._conversation_log = conversation_log

    async def load_history(self, user_id: str, current_input: str, message_type: str) -> list[AnyMessage]:
        """從 Redis 載入歷史並轉換為 LangChain 格式的 Message 列表"""
        history = await self._repo.list_messages(user_id)
        recent_history = history[-RECENT_CONTEXT_MESSAGES:]

        chat_history: list[AnyMessage] = [
            AIMessage(content=msg.content)
            if msg.message_type == "assistant_reply"
            else HumanMessage(content=msg.content)
            for msg in recent_history
        ]

        # 地理位置訊息只在當前輪次供 AI 參考，不寫入 Redis
        if message_type == "location":
            chat_history.append(HumanMessage(content=current_input))

        # 防禦性保底：確保至少有當前輸入
        if not chat_history:
            chat_history.append(HumanMessage(content=current_input))

        return chat_history

    async def save_turn(self, user_id: str, user_text: str, ai_reply: str, message_type: str, event_time: datetime) -> None:
        """成功回覆後，把這一輪對話（User & AI）寫進正式紀錄與 Redis 快取"""
        if message_type == "location":
            # 地理位置訊息不儲存至歷史庫
            return

        user_msg = ChatMessage(
            line_id=user_id,
            message_type=message_type,
            content=user_text,
            timestamp=event_time,
        )
        ai_msg = ChatMessage(
            line_id=user_id,
            message_type="assistant_reply",
            content=ai_reply,
            timestamp=datetime.now(timezone.utc),
        )

        # 先寫正式紀錄：Redis 出錯會往上拋，不能連帶丟掉這一輪的紀錄。
        # 這時回覆已經送出，紀錄寫失敗只能留 log，不能讓這一輪變成錯誤。
        try:
            await self._conversation_log.append_message(user_id, user_msg)
            await self._conversation_log.append_message(user_id, ai_msg)
        except Exception:
            logger.exception("寫入對話紀錄失敗 line_id=%s", user_id)

        await self._repo.append_message(user_id, user_msg)
        await self._repo.append_message(user_id, ai_msg)
