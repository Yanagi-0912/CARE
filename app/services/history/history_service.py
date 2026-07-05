from datetime import datetime, timezone
from typing import Any
from langchain_core.messages import HumanMessage, AIMessage, AnyMessage

from app.models.chat_message import ChatMessage


class LineMessageHistoryService:
    """歷史對話記憶服務：負責 Redis 聊天紀錄與 LangChain Message 物件的轉換與儲存。"""
    
    def __init__(self, chat_history_repository):
        self._repo = chat_history_repository

    async def load_history(self, user_id: str, current_input: str, message_type: str) -> list[AnyMessage]:
        """從 Redis 載入歷史並轉換為 LangChain 格式的 Message 列表"""
        history = await self._repo.list_messages(user_id)
        # 只取最後 5 筆訊息作為上下文，以保留完整 Redis 紀錄供諮詢摘要使用
        recent_history = history[-5:] if len(history) > 5 else history

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
        """成功回覆後，儲存當前的這一輪對話（User & AI）到 Redis 中"""
        if message_type == "location":
            # 地理位置訊息不儲存至歷史庫
            return

        # 儲存 User 訊息
        user_msg = ChatMessage(
            line_id=user_id,
            message_type=message_type,
            content=user_text,
            timestamp=event_time,
        )
        await self._repo.append_message(user_id, user_msg)

        # 儲存 AI 回覆訊息
        ai_msg = ChatMessage(
            line_id=user_id,
            message_type="assistant_reply",
            content=ai_reply,
            timestamp=datetime.now(timezone.utc),
        )
        await self._repo.append_message(user_id, ai_msg)
