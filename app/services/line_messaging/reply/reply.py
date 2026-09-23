"""LINE Messaging API Channel Access Token 與回覆管理。

負責向 LINE OAuth 換取 Bot 用的 access token 並作快取，以及建構回覆訊息（包含 Text、Audio/TTS）。
"""

from __future__ import annotations

import asyncio
import json
import logging

from app.core.request_logging import stage_timer
from app.services.line_messaging.reply.tts_service import public_audio_url
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import requests

from linebot.v3.messaging import (
    ApiClient,
    AudioMessage,
    Configuration,
    FlexContainer,
    FlexMessage,
    LocationAction,
    MessageAction,
    MessagingApi,
    PostbackAction,
    PushMessageRequest,
    QuickReply,
    QuickReplyItem,
    ReplyMessageRequest,
    TextMessage,
)

from app.core.config import settings
from app.core.medication_facts import get_request_medication_facts
from app.core.rag_sources import get_request_rag_sources
from app.i18n.messages import strip_rag_prefix, strip_sources_section, t
from app.services.line_messaging.flex.rag_answer_flex import (
    build_document_answer_flex,
    build_medication_answer_flex,
    build_rag_answer_flex,
)
from app.services.line_messaging.flex.table_flex import build_table_flex_from_text
from app.services.line_messaging.send_result import (
    SendOutcome,
    SendResult,
    classify_send_exception,
)
from app.services.line_messaging.token_manager import LineTokenManager
from resources.flex_messages.size_guard import fits
from resources.flex_messages.theme import resolve_theme

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[LineReplier]"
DEFAULT_AUDIO_DURATION_MS = 60_000


def _strip_medication_facts(card_text: str, block: str) -> str:
    """把登記資料那一整段從卡片本文裡拿掉——它在卡片上有自己的一塊。

    比對的是 `MedicationQuestionService` 組出來的原字串，不是逐行刪：那一段由
    一句引言加上幾行事實組成，逐行刪會留下一句沒有內容的「以下是 CARE 裡登記
    的資料：」。

    找不到就原樣回傳：卡片多出一段重複的文字，比為了排版丟掉答案本文好。
    """
    if not block:
        return card_text
    return card_text.replace(block, "").strip()


class LineReplier:
    """負責組織回覆內容並呼叫 LINE SDK 送出。"""

    def __init__(self, token_manager: LineTokenManager, tts_service=None) -> None:
        self._token_manager = token_manager
        self._tts_service = tts_service

    async def reply(
        self,
        reply_token: str,
        message_text: str,
        user_id: str,
        request_location: bool = False,
        voice_reply_enabled: bool = True,
        language: str | None = None,
        voice_rate: str = "normal",
        voice_gender: str = "female",
        answer_kind: str | None = None,
        user_question: str = "",
        image_text: str = "",
        speech_language: str | None = None,
        lost_help_postback: str | None = None,
    ) -> bool:
        """發送 LINE 訊息（包含文字訊息、Flex Message 與選填的 TTS 語音訊息）

        `image_text` 是圖片辨識的原文（不含媒體前綴）；裡面有表格時，回答前面
        會多送一張表格卡。

        `speech_language` 是語音用的語言：選台語的使用者文字是 zh-TW、語音是
        nan-TW。沒給就跟 `language` 相同。

        `lost_help_postback` 有值時，快速回覆多一顆「我迷路了，通知家人」，按下送出
        這段 postback data（走失分類器沒把握時用，見 app/services/lost/lost_classifier.py）。
        """
        tts_language = speech_language or language
        # 文字先送、語音後推：合成要 5 秒起跳（線上 14 天 p50 5.3 秒、p90 11.5
        # 秒、最差 30 秒），以前在 reply_message 之前 await 它，開語音的使用者
        # （173/319 則）看到答案的時間就是 agent 之後再加這一整段。現在文字卡
        # 用 reply token 立刻送出，音檔合成完再用 push 補一則。代價是每則語音
        # 回覆多吃一則 push 額度（2026-09-22 James 決定接受）。
        tts_text: str | None = None
        try:
            if not reply_token or not reply_token.strip():
                raise ValueError("LINE 事件缺少 reply_token")
            if not user_id or not user_id.strip():
                raise ValueError("LINE 事件缺少 user_id")

            message_text = self._normalize_message_text(message_text)
            logger.info(
                f"{LOGGER_HEADER_TEXT} 準備回覆，user_id=%s, request_location=%s",
                user_id,
                request_location,
            )

            # 工具自產的 Flex（verify_claim、open_official_site）原樣送出。
            # 語音則看工具有沒有附朗讀稿：卡片 JSON 本身不能拿去合成，而只有
            # 工具知道哪一段是給耳朵的——判定卡有實質結論所以給，官網卡那種
            # 只有幾顆連結按鈕的就不給。沒附的維持原本的無語音行為。
            tool_flex, tool_speech_text = self._try_parse_flex_message(message_text)
            if tool_flex is not None:
                logger.info(
                    f"{LOGGER_HEADER_TEXT} 解析為工具 Flex Message，將以 Flex 形式回覆"
                )
                messages = [tool_flex]
                # 分享卡附的加好友連結緊接在卡片後面（理由見 _tool_follow_up_text）。
                follow_up_text = self._tool_follow_up_text(message_text)
                if follow_up_text:
                    messages.append(TextMessage(text=follow_up_text))
                if tool_speech_text:
                    tts_text = tool_speech_text
            else:
                answer_card, card_text = self._build_answer_card(
                    message_text, answer_kind, user_question
                )
                if answer_card is not None:
                    logger.info(
                        f"{LOGGER_HEADER_TEXT} 已組成 %s 回答卡，將以 Flex 形式回覆",
                        answer_kind,
                    )
                    messages = [answer_card]
                    # 卡片路徑同樣有語音：只有純文字分支有語音的話，開了語音
                    # 回覆的使用者會在 RAG 回覆上靜默失去這個功能。合成用的是
                    # 組卡前的純文字，不是卡片 JSON。
                    tts_text = card_text
                else:
                    logger.info(
                        f"{LOGGER_HEADER_TEXT} 未組成卡片，將以純文字回覆"
                    )
                    text_message = TextMessage(text=message_text)
                    messages = [text_message]
                    tts_text = message_text

            # 表格卡排在回答前面：回答是照這份辨識結果寫的，長輩要先看到機器讀到
            # 什麼，才核對得出讀錯的地方。插在最前面，下面的 quickReply 就照舊掛在
            # 回答（或語音）上。加上它最多三則，在 LINE 單次回覆五則的上限內。
            table_card = self._build_table_card(image_text)
            if table_card is not None:
                logger.info(f"{LOGGER_HEADER_TEXT} 圖片辨識出表格，回答前附上表格卡")
                messages.insert(0, table_card)

            # quickReply 只會顯示在陣列最後一則訊息上，因此統一在此處掛到最後一則。
            # 語音那則之後另外 push，會再掛同一組（見 _push_tts_audio）——LINE 只
            # 顯示聊天室最後一則的 quickReply，音檔到了按鈕不能跟著消失。
            quick_items = []
            if request_location:
                qr_label = t("location.share_qr_label", language=language)
                quick_items.append(QuickReplyItem(action=LocationAction(label=qr_label)))
            if lost_help_postback:
                lost_label = t("lost.help.quick_reply", language=language)
                quick_items.append(
                    QuickReplyItem(
                        action=PostbackAction(
                            label=lost_label,
                            data=lost_help_postback,
                            displayText=t("lost.help.display", language=language),
                        )
                    )
                )
            if quick_items and messages:
                messages[-1].quick_reply = QuickReply(items=quick_items)
            elif len(messages) > 1 and getattr(messages[0], "quick_reply", None):
                # 工具自帶的 quickReply（電視新聞回問台別）原本掛在卡片上，但
                # 卡片後面還會接 followUpText 與語音，而 LINE 只顯示**最後一則**
                # 的 quickReply——不搬過去，按鈕就靜靜消失了。
                messages[-1].quick_reply = messages[0].quick_reply
                messages[0].quick_reply = None

        except Exception:
            # 組訊息就失敗（卡片、token）：使用者不能什麼都收不到。
            logger.exception("Failed to build LINE reply for user %s", user_id)
            messages = [TextMessage(text=t("line.fallback_process_error", language=language))]
            tts_text = None

        ok, delivered = await self._reply_or_push_result(
            reply_token, user_id, messages, language=language
        )
        # 只有原本那組訊息真的送到了才補語音；退到「發生錯誤」那句時念答案
        # 只會讓人更糊塗。
        if delivered and tts_text and voice_reply_enabled and self._tts_service is not None:
            await self._push_tts_audio(
                user_id,
                tts_text,
                language=tts_language,
                voice_rate=voice_rate,
                voice_gender=voice_gender,
                quick_reply=getattr(messages[-1], "quick_reply", None) if messages else None,
            )
        return ok

    async def _reply_or_push(
        self,
        reply_token: str,
        user_id: str,
        messages: list,
        *,
        language: str | None,
    ) -> bool:
        ok, _ = await self._reply_or_push_result(
            reply_token, user_id, messages, language=language
        )
        return ok

    async def _reply_or_push_result(
        self,
        reply_token: str,
        user_id: str,
        messages: list,
        *,
        language: str | None,
    ) -> tuple[bool, bool]:
        """先用 reply token 回；回不了就改 push，最後至少推一句錯誤說明。

        回 (有沒有送出任何東西, 原本那組訊息有沒有送到)。兩者只在最後退到
        錯誤說明那一步時不同。

        reply token 一分鐘內有效且只能用一次；agent 跑久、LINE 重送、或前面
        某一步已經消耗掉 token，reply 都會被 LINE 以 400 拒絕。以前這裡只記
        log 回 False，使用者看到讀取動畫消失後就是一片沉默。改成：

        1. reply 失敗（不論原因）→ 同一組訊息改用 push 送。
        2. push 也失敗（內容本身不合法、或額度用完）→ 推一句純文字錯誤說明。
           額度用完時這一句也送不出去，那是真的沒辦法，記 log 即可。
        """
        result = await self._send_reply(reply_token, messages)
        if result.ok:
            logger.debug("Message sent to LINE for user %s", user_id)
            return True, True

        logger.warning(
            f"{LOGGER_HEADER_TEXT} reply 失敗（%s, status=%s），改用 push 補送 user_id=%s",
            result.outcome.value,
            result.status,
            user_id,
        )
        if result.outcome is SendOutcome.QUOTA_EXCEEDED:
            # reply 不計額度，429 只會是短時間打太快；push 會吃額度，不值得補。
            return False, False

        pushed = await self._send_push(user_id, messages)
        if pushed.ok:
            return True, True
        if pushed.outcome in (SendOutcome.QUOTA_EXCEEDED, SendOutcome.UNAUTHORIZED):
            return False, False

        # 同一組訊息 push 也被拒，多半是內容不合法（Flex 太大、欄位錯）。
        # 至少讓使用者知道這一輪沒有回覆，而不是無聲無息。
        fallback = await self._send_push(
            user_id,
            [TextMessage(text=t("line.fallback_process_error", language=language))],
        )
        return fallback.ok, False

    async def reply_flex(
        self, reply_token: str, flex_message: FlexMessage, user_id: str
    ) -> bool:
        """回覆 LINE Flex Message"""
        if not reply_token or not reply_token.strip():
            logger.error("LINE 事件缺少 reply_token，Flex 改用 push 送給 %s", user_id)
            return (await self._send_push(user_id, [flex_message])).ok
        ok = await self._reply_or_push(
            reply_token, user_id, [flex_message], language=None
        )
        if ok:
            logger.info("Flex Message replied to LINE user %s", user_id)
        return ok

    async def reply_messages(
        self, reply_token: str, user_id: str, messages: list, *, language: str | None = None
    ) -> bool:
        """回覆一組現成的訊息（例如帶 postback 快速回覆的文字）。

        `reply` 會把字串改寫成卡片、附語音；這支原樣送出，失敗時同樣改 push。
        """
        if not reply_token or not reply_token.strip():
            return (await self._send_push(user_id, messages)).ok
        return await self._reply_or_push(reply_token, user_id, messages, language=language)

    async def push_flex(self, user_id: str, flex_message: FlexMessage) -> bool:
        """主動推播 LINE Flex Message"""
        return (await self.push_flex_result(user_id, flex_message)).ok

    async def push_flex_result(
        self, user_id: str, flex_message: FlexMessage
    ) -> SendResult:
        """同 push_flex，但回傳分類過的結果。

        排程器用這支分辨 429（額度用完，不該記成已送、也不該算失敗次數）
        與 400（對象無效，重試沒有意義）。
        """
        result = await self._send_push(user_id, [flex_message])
        if result.ok:
            logger.info("Flex Message pushed to LINE user %s", user_id)
        return result

    async def push_text(self, user_id: str, text: str) -> bool:
        """主動推播純文字訊息。

        背景通知（例如用藥風險提醒）沒有 reply token 可用，也不該佔用主回覆的
        reply token。純文字而非 Flex：這些訊息是說給當事人聽的一段話，不是卡片。
        """
        return (await self.push_text_result(user_id, text)).ok

    async def push_text_result(self, user_id: str, text: str) -> SendResult:
        """同 push_text，但回傳分類過的結果（用途見 push_flex_result）。"""
        result = await self._send_push(user_id, [TextMessage(text=text)])
        if result.ok:
            logger.info("Text message pushed to LINE user %s", user_id)
        return result

    async def push_messages(self, user_id: str, messages: list) -> SendResult:
        """推播任意一組訊息（最多五則，LINE 的單次上限）。"""
        return await self._send_push(user_id, messages)

    # ------------------------------------------------------------------
    # 真正打 LINE API 的兩支：所有 reply／push 都經過這裡。
    #
    # `MessagingApi` 是同步 SDK（底層 urllib3），直接在 async def 裡呼叫會把
    # 整個事件迴圈凍住——LINE 端一次網路停滯就讓其他使用者的 webhook、LIFF
    # API、/health 探針一起停擺。所以一律 to_thread，並帶 _request_timeout。
    # ------------------------------------------------------------------

    async def _send_reply(self, reply_token: str, messages: list) -> SendResult:
        if not reply_token or not reply_token.strip():
            return SendResult(SendOutcome.REJECTED, None, "missing reply_token")
        request = ReplyMessageRequest(replyToken=reply_token, messages=messages)
        return await self._call_line_api("reply_message", request, log_name="reply")

    async def _send_push(self, user_id: str, messages: list) -> SendResult:
        if not user_id or not user_id.strip():
            return SendResult(SendOutcome.REJECTED, None, "missing user_id")
        request = PushMessageRequest(to=user_id, messages=messages)
        return await self._call_line_api("push_message", request, log_name="push")

    async def _call_line_api(
        self, method_name: str, request: Any, *, log_name: str
    ) -> SendResult:
        try:
            access_token = await self._token_manager.get_token_async()
        except Exception:
            logger.exception("取得 channel access token 失敗，%s 未送出", log_name)
            return SendResult(SendOutcome.UNAUTHORIZED, None, "token unavailable")

        def _do_call() -> None:
            line_config = Configuration(access_token=access_token)
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                getattr(line_bot_api, method_name)(
                    request, _request_timeout=settings.LINE_API_TIMEOUT_SECONDS
                )

        try:
            await asyncio.to_thread(_do_call)
            return SendResult.success()
        except Exception as exc:
            result = classify_send_exception(exc)
            if result.outcome is SendOutcome.UNAUTHORIZED:
                # token 已被 LINE 撤銷：清掉快取，下一次呼叫會重新換取。
                self._token_manager.invalidate()
            logger.error(
                f"{LOGGER_HEADER_TEXT} LINE %s 失敗 outcome=%s status=%s detail=%s",
                log_name,
                result.outcome.value,
                result.status,
                result.detail,
                exc_info=result.outcome is SendOutcome.TRANSIENT,
            )
            return result


    def _build_answer_card(
        self, message_text: str, answer_kind: Optional[str], user_question: str
    ) -> tuple[Optional[FlexMessage], str]:
        """把 RAG 回覆組成卡片。

        回傳 `(卡片, 卡片內用的純文字)`；組不出來、太大或 answer_kind 為 None
        時卡片為 None。純文字一併回傳是給 TTS 用的——朗讀的內容應與卡片一致。

        任何失敗都退回純文字而非拋出：呈現層是最後一步，使用者寧可拿到樸素
        的文字，也不能拿到空白回覆。
        """
        if answer_kind not in ("rag", "document", "medication"):
            return None, message_text

        # 前綴由卡片 header 取代；來源清單移到按鈕，留在內文會重複一次。
        card_text = strip_sources_section(strip_rag_prefix(message_text)).strip()

        try:
            ft = resolve_theme()
            if answer_kind == "rag":
                card = build_rag_answer_flex(
                    user_question, card_text, get_request_rag_sources(), ft
                )
            elif answer_kind == "medication":
                facts = get_request_medication_facts()
                # 登記資料在卡片上有自己的一塊，留在本文會整段重複一次。
                card = build_medication_answer_flex(
                    user_question,
                    _strip_medication_facts(card_text, facts.block if facts else ""),
                    list(facts.lines) if facts else [],
                    get_request_rag_sources(),
                    ft,
                )
            else:
                card = build_document_answer_flex(user_question, card_text, ft)

            if not fits(card.to_dict()["contents"]):
                logger.warning(
                    f"{LOGGER_HEADER_TEXT} %s 回答卡超過大小上限，改以純文字回覆",
                    answer_kind,
                )
                return None, message_text

            return card, card_text
        except Exception:
            logger.warning(
                f"{LOGGER_HEADER_TEXT} %s 回答卡組裝失敗，改以純文字回覆",
                answer_kind,
                exc_info=True,
            )
            return None, message_text

    def _build_table_card(self, image_text: str) -> Optional[FlexMessage]:
        """圖片辨識文字裡有 Markdown 表格時組成表格卡，沒有就回 None。

        在 replier 組而不是在 media handler：卡片要吃使用者的字級，而字級要等
        `_process_and_reply` 載入個人檔案、寫進 request context 之後才讀得到
        （同 `_build_answer_card`）。

        任何失敗都回 None：表格卡是附在回答前面的，不能因為它讓整則回覆送不出去。
        """
        if not image_text:
            return None
        try:
            return build_table_flex_from_text(image_text, resolve_theme())
        except Exception:
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 表格卡組裝失敗，只送回答", exc_info=True
            )
            return None

    @staticmethod
    def _try_parse_flex_message(
        message_text: str,
    ) -> tuple[Optional[FlexMessage], str]:
        """解析工具自產的 Flex JSON，回傳 `(卡片, 朗讀稿)`。

        朗讀稿取自選填的頂層鍵 `speechText`，沒有就是空字串。它必須由工具提供
        而不能由這裡從卡片反解：這條路徑上 replier 拿到的只有 Flex JSON，組卡
        前的純文字沒有跨過 agent 邊界（對照 `_build_answer_card`，那條路是
        replier 自己組卡，所以純文字還在手上）。從已組好的卡片節點反解文字是
        另一個坑，理由同 `rag_sources.SourceRef` 的 docstring。

        `speechText` 只被讀走、不會進到送出的 FlexMessage——LINE 的 payload 裡
        沒有這個欄位。
        """
        if not message_text or not isinstance(message_text, str):
            return None, ""

        text_strip = message_text.strip()
        if not (text_strip.startswith("{") and text_strip.endswith("}")):
            return None, ""

        try:
            data = json.loads(text_strip)
        except json.JSONDecodeError:
            logger.debug(f"{LOGGER_HEADER_TEXT} 文字內容不是有效 JSON，略過 Flex 解析")
            return None, ""

        if not isinstance(data, dict):
            return None, ""

        if data.get("type") == "flex" and "contents" in data:
            alt_text = data.get("altText") or "醫療院所查詢結果"
            contents = FlexContainer.from_dict(data["contents"])
            speech_text = data.get("speechText")
            speech_text = speech_text.strip() if isinstance(speech_text, str) else ""
            # 工具以純 dict 描述 quickReply（科別建議卡的「附近哪裡有○○科」），
            # SDK 需要的是物件。少了這行轉換，按鈕會被無聲丟掉——卡片照常送出、
            # 按鈕不出現、也沒有任何錯誤訊息。
            quick_reply = LineReplier._parse_quick_reply(data.get("quickReply"))
            logger.info(
                f"{LOGGER_HEADER_TEXT} Flex JSON 解析成功，altText=%s, has_speech=%s, "
                "has_quick_reply=%s",
                alt_text,
                bool(speech_text),
                quick_reply is not None,
            )
            return (
                FlexMessage(
                    altText=alt_text, contents=contents, quickReply=quick_reply
                ),
                speech_text,
            )

        return None, ""

    @staticmethod
    def _tool_follow_up_text(message_text: str) -> str:
        """工具 Flex 選填的頂層鍵 `followUpText`：緊接在卡片後面送出的一則純文字。

        分享卡用它附上加好友連結——卡片裡的字不能長按複製，純文字才能複製、
        轉貼。跟 `speechText` 一樣只被讀走，不會進到送出的 FlexMessage。另寫
        一支而不是擴充 `_try_parse_flex_message` 的回傳值，是因為那個二元組
        已有呼叫端依賴（test_symptom_department_flex）。
        """
        try:
            data = json.loads(message_text)
        except (TypeError, ValueError):
            return ""
        text = data.get("followUpText") if isinstance(data, dict) else None
        return text.strip() if isinstance(text, str) else ""

    @staticmethod
    def _parse_quick_reply(payload: Any) -> Optional[QuickReply]:
        #把 Flex payload 裡的 quickReply 轉成 SDK 物件。
        if not isinstance(payload, dict):
            return None
        items = []
        for raw_item in payload.get("items") or ():
            action = raw_item.get("action") if isinstance(raw_item, dict) else None
            if not isinstance(action, dict) or action.get("type") != "message":
                continue
            label, text = action.get("label"), action.get("text")
            if not label or not text:
                continue
            items.append(QuickReplyItem(action=MessageAction(label=label, text=text)))
        if not items:
            if payload:
                logger.warning(
                    f"{LOGGER_HEADER_TEXT} quickReply 內容無法解析，已略過：%r", payload
                )
            return None
        return QuickReply(items=items)

    @staticmethod
    def _normalize_message_text(message_text: Any) -> str:
        if isinstance(message_text, str):
            return message_text
        if isinstance(message_text, list):
            return "".join(
                (
                    part
                    if isinstance(part, str)
                    else (part.get("text", "") if isinstance(part, dict) else str(part))
                )
                for part in message_text
            )
        if message_text is None:
            return ""
        return str(message_text)


    async def _push_tts_audio(
        self,
        user_id: str,
        message_text: str,
        *,
        language: str | None = None,
        voice_rate: str = "normal",
        voice_gender: str = "female",
        quick_reply: QuickReply | None = None,
    ) -> bool:
        """文字送出之後才合成語音、用 push 補一則。

        失敗只記 log：文字已經送到，語音是附加的。ok 欄位分開記：失敗會轉
        gTTS 備援或整個放棄，兩者的耗時意義不同。`quick_reply` 是文字那則掛
        的同一組按鈕——LINE 只顯示聊天室最後一則的 quickReply，音檔一到，
        「分享位置」「我迷路了」就會從畫面上消失，所以要再掛一次。
        """
        if not message_text or self._tts_service is None:
            return False
        try:
            with stage_timer(
                logger, "reply_tts", chars=len(message_text or ""), ok="False"
            ) as t_tts:
                _audio_bytes, output, duration_ms = await self._tts_service.synthesize(
                    message_text,
                    language=language or "zh-TW",
                    voice_rate=voice_rate,
                    voice_gender=voice_gender,
                )
                t_tts["ok"] = "True"
            audio_url = self._resolve_audio_url(output)
        except Exception:
            logger.exception("TTS generation failed; text reply already sent.")
            return False
        if not audio_url:
            return False

        audio = AudioMessage(
            original_content_url=audio_url,
            duration=int(duration_ms or DEFAULT_AUDIO_DURATION_MS),
            quick_reply=quick_reply,
        )
        with stage_timer(logger, "reply_tts_push", ok="False") as t_push:
            result = await self._send_push(user_id, [audio])
            t_push["ok"] = str(result.ok)
        if not result.ok:
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 語音補送失敗（%s, status=%s）user_id=%s",
                result.outcome.value,
                result.status,
                user_id[:10],
            )
        return result.ok

    @staticmethod
    def _resolve_audio_url(output: str) -> Optional[str]:
        return public_audio_url(output)
