import json
import logging
from datetime import datetime, timezone
from langchain_core.messages import HumanMessage, AIMessage, AnyMessage

from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import t
from app.models.chat_message import ChatMessage

logger = logging.getLogger(__name__)

# agent 每輪只帶最近這麼多則訊息當上下文；Redis 快取也只保留這麼多（見 dependencies）。
RECENT_CONTEXT_MESSAGES = 5

# 「我無法理解您的問題」是 agent 回空時 message_handler 補上的保底句，六種語言
# 各一句。它不是 AI 對這一輪說了什麼，存進歷史只會讓下一輪的 agent 看到自己
# 「上一輪聽不懂」，跟著再回一次聽不懂。用固定字串比對就夠：這句話是本系統
# 自己產生的常數，不是外部輸入。
_UNUNDERSTOOD_REPLIES: frozenset[str] = frozenset(
    t("line.fallback_ununderstood", lang) for lang in SUPPORTED_LANGUAGES
)


def flex_history_placeholder(ai_reply: str) -> str | None:
    """工具自產的 Flex JSON（院所清單卡、緊急卡、分享卡…）在歷史裡的替身。

    回覆是 Flex JSON 時回一句短文字，否則回 None。判斷方式與
    `LineReplier._try_parse_flex_message` 相同：頂層 dict、type=flex、有 contents。

    為什麼不能原樣存：這段 JSON 會被 `load_history` 包成 AIMessage 餵回 agent，
    一張院所清單卡動輒數千字，佔掉整個上下文視窗不說，模型還會模仿它、在下一輪
    自己吐出半截 JSON。altText 是卡片對「看不到卡片的人」的說明，正好也是
    對模型最有用的一句話。
    """
    text = (ai_reply or "").strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("type") != "flex" or "contents" not in data:
        return None
    alt_text = str(data.get("altText") or "").strip()
    return f"[已回覆卡片：{alt_text}]" if alt_text else "[已回覆卡片]"


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
        ai_msg = self._ai_message_for_history(user_id, ai_reply)

        # 先寫正式紀錄：Redis 出錯會往上拋，不能連帶丟掉這一輪的紀錄。
        # 這時回覆已經送出，紀錄寫失敗只能留 log，不能讓這一輪變成錯誤。
        try:
            await self._conversation_log.append_message(user_id, user_msg)
            if ai_msg is not None:
                await self._conversation_log.append_message(user_id, ai_msg)
        except Exception:
            logger.exception("寫入對話紀錄失敗 line_id=%s", user_id)

        await self._repo.append_message(user_id, user_msg)
        if ai_msg is not None:
            await self._repo.append_message(user_id, ai_msg)

    @staticmethod
    def _ai_message_for_history(user_id: str, ai_reply: str) -> ChatMessage | None:
        """決定這一輪 AI 那半要存什麼：原文、Flex 的替身、或不存。

        兩種替換都同時套在正式紀錄與快取上：正式紀錄是給人查的，一張 Flex JSON
        在那裡一樣只是幾千字的雜訊，替身反而看得出這一輪回了什麼卡。
        """
        placeholder = flex_history_placeholder(ai_reply)
        if placeholder is not None:
            content = placeholder
        elif (ai_reply or "").strip() in _UNUNDERSTOOD_REPLIES:
            return None
        else:
            content = ai_reply
        return ChatMessage(
            line_id=user_id,
            message_type="assistant_reply",
            content=content,
            timestamp=datetime.now(timezone.utc),
        )
