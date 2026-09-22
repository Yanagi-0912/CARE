import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from linebot.v3.webhooks import MessageEvent, TextMessageContent

from app.core.config import settings
from app.core.request_logging import log_stage
from app.core.rag_sources import begin_request_rag_sources, reset_request_rag_sources
from app.core.user_font_size import (
    normalize_user_font_size,
    reset_request_font_size,
    set_request_font_size,
)
from app.core.user_age import reset_request_age, set_request_age
from app.services.safety.emergency_alert_service import (
    notify_patient_family_was_told,
)
from app.core.user_language import (
    DEFAULT_USER_LANGUAGE,
    normalize_language_choice,
    normalize_user_language,
    reset_request_language,
    set_request_language,
)
from app.i18n.messages import t
from app.services.line_messaging.reply.reply import LineReplier
from app.services.line_messaging.clinic_recording_intent import (
    is_clinic_recording_intent,
)
from app.services.line_messaging.share_intent import is_share_intent
from linebot.v3.messaging import FlexContainer, FlexMessage

from resources.flex_messages.lost_location_flex_message import (
    build_no_family_flex,
    lost_confirm_postback_data,
)
from resources.flex_messages.medical_messages.emergency_condition_flex_message import (
    build_emergency_condition_flex,
)
from app.core.request_context import reset_line_user_id, set_line_user_id

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Handler:MessageHandler]"


class LineValidationError(Exception):
    """LINE 訊息欄位格式或內容驗證失敗。"""


class BaseLineMessageHandler:
    """LINE 訊息處理器的基底類別，處理共通的 Agent 呼叫、歷史紀錄與 Reply 調用邏輯。"""

    def __init__(
        self,
        agent,
        history_service,
        user_profile_service,
        replier: LineReplier,
        loading_animation_service=None,
        safety_alert_service=None,
        emergency_family_alert_service=None,
        share_card_service=None,
        lost_location_service=None,
        urgency_classifier=None,
        clinic_recording_flow=None,
    ):
        self._agent = agent
        self._history_service = history_service
        self._user_profile_service = user_profile_service
        self._replier = replier
        self._loading_animation_service = loading_animation_service
        # 功能關閉時 dependencies 根本不會組出這個服務，這裡就是 None，
        # 整條路徑一步都不會執行（見 app/dependencies.py 的組裝）。
        self._safety_alert_service = safety_alert_service
        self._emergency_family_alert_service = emergency_family_alert_service
        # 沒注入時（媒體訊息的 handler 就沒有）分享卡的秒回路徑整段不執行。
        self._share_card_service = share_card_service
        # 沒注入時「我走丟了」照一般訊息進 agent。
        self._lost_location_service = lost_location_service
        # 走失流程不進 agent，也就跳過了 agent 裡的急迫度判斷；這裡補跑同一個判斷器
        # （見 start_lost_flow）。沒注入時不補跑。
        self._urgency_classifier = urgency_classifier
        # 打「看診錄音」直接進錄音流程。沒注入時照一般訊息進 agent。
        self._clinic_recording_flow = clinic_recording_flow
        # 併行任務要被持有參考直到完成，否則可能在跑完之前就被 GC 回收。
        self._safety_alert_tasks: set[asyncio.Task] = set()
        self._loading_animation_tasks: set[asyncio.Task] = set()

    def _schedule_loading_animation(self, user_id: str) -> None:
        """讀取動畫丟到背景開，不擋主流程。

        它是一次 LINE API 呼叫（換 token＋HTTP 往返），以前 await 在 agent 之前，
        文字訊息等一次、媒體訊息等兩次。動畫只是附加效果，開不開成功都不該讓
        使用者多等；失敗只記 log（start 自己就會吃掉例外）。
        """
        if self._loading_animation_service is None or not user_id:
            return

        async def _run() -> None:
            try:
                await self._loading_animation_service.start(user_id)
            except Exception:  # noqa: BLE001 - 背景旁路，例外不得逸散
                logger.warning("讀取動畫啟動失敗", exc_info=True)

        task = asyncio.create_task(_run())
        self._loading_animation_tasks.add(task)
        task.add_done_callback(self._loading_animation_tasks.discard)

    async def _process_and_reply(
        self,
        event: MessageEvent,
        user_text: str,
        message_type: str,
        *,
        image_text: str = "",
        speech_language: str | None = None,
        user_profile: dict | None = None,
    ) -> None:
        """`speech_language` 是這一則語音實際聽出來的語言（台語或華語），語音訊息
        才有。有值時它蓋過使用者設定的語言，決定回覆要用哪一種念：講台語就用台語
        念回去，不必先到設定頁切語言。文字訊息沒有音訊可判，仍照設定。

        `user_profile` 是 dispatcher 決定語言時已經讀過的那份；有給就不再查一次
        Mongo。沒給（或讀不到、或新使用者）才在這裡查，並與歷史載入併行。
        """
        user_id = getattr(event.source, "user_id", "")
        reply_token = getattr(event, "reply_token", "")
        event_time = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)

        user_language = DEFAULT_USER_LANGUAGE
        lang_token = None
        font_token = None
        age_token = None
        rag_sources_token = None

        try:
            log_stage(
                logger,
                "handle",
                type=message_type,
                text_len=len(user_text or ""),
            )

            # 用藥風險評估與主回覆併行。放在這裡是因為此處是文字與媒體訊息的
            # 共用匯流點，且媒體的 OCR 文字已經備妥——判定所需的訊號全在字串
            # 裡，不需要再碰一次影像。它不參與下面任何一步，主回覆的內容與
            # 時序完全不受影響。
            self._schedule_safety_alert_check(user_id, user_text)

            t0 = time.perf_counter()
            history_coro = self._history_service.load_history(
                user_id=user_id,
                current_input=user_text,
                message_type=message_type,
            )
            if user_profile is None and self._user_profile_service:
                # 兩筆互不相干的讀取（Redis 歷史、Mongo 檔案）一起等。
                chat_history, user_profile = await asyncio.gather(
                    history_coro, self._user_profile_service.get_user_profile(user_id)
                )
            else:
                chat_history = await history_coro
            log_stage(
                logger,
                "history_loaded",
                turns=len(chat_history or []),
                ms=int((time.perf_counter() - t0) * 1000),
            )

            # 文字與語音可能不同：選台語的使用者文字是 zh-TW、語音是 nan-TW。
            # ContextVar 存使用者的選擇，get_request_language() 取出來的是文字語言。
            language_choice = speech_language or self._language_choice_from_profile(
                user_profile
            )
            user_language = normalize_user_language(language_choice)
            lang_token = set_request_language(language_choice)
            # Agent 產生的 Flex Message 走 LangChain tool，拿不到 user_profile，
            # 因此字級也比照語言存進 request-scoped ContextVar
            font_token = set_request_font_size(
                self._font_size_from_profile(user_profile)
            )
            # 年齡同理：症狀科別建議要靠它決定該不該給兒科，而那段程式在
            # LangChain tool 底下，拿不到 user_profile。
            age_token = set_request_age((user_profile or {}).get("age"))

            # 走失求救：「我走丟了」「傳位置給家人」直接回定位卡並通知家人、不進
            # agent（理由見 lost_intent）。語音也要攔：慌張的長輩最可能用講的。
            # 位置與字級一樣排在語言設好之後，卡片才會照使用者的設定。
            lost_help_postback = None
            if (
                message_type in ("text", "audio")
                and self._lost_location_service is not None
            ):
                detection = self._lost_location_service.detect_intent(user_text)
                font_size = self._font_size_from_profile(user_profile)
                if detection.intent is not None and detection.needs_confirmation:
                    # 分類器沒把握：不攔，照常進 agent，回覆下方多一顆「我迷路了，
                    # 通知家人」。不先問「你是不是迷路了？」：沒把握的訊息裡有急症
                    # 與自傷（「叫不醒」「我站在頂樓」），攔下來問就拿不到紅卡。
                    lost_help_postback = lost_confirm_postback_data(user_text)
                    log_stage(
                        logger, "lost_detect", outcome="button", source=detection.source,
                        p=round(detection.probability or 0.0, 4),
                    )
                elif detection.intent is not None:
                    log_stage(
                        logger, "lost_detect", source=detection.source,
                        p=None if detection.probability is None else round(detection.probability, 4),
                    )
                    await self.start_lost_flow(
                        user_id=user_id,
                        reply_token=reply_token,
                        user_text=user_text,
                        intent=detection.intent,
                        language=user_language,
                        font_size=font_size,
                    )
                    return

            # 分享卡：常見說法直接回卡、不進 agent（理由見 share_intent）。排在語言
            # 與字級設好之後，卡片才會照使用者的設定；排在 rag 來源 holder 與讀取
            # 動畫之前，那兩樣只有 agent 那條路用得到。只看使用者親手打的字——
            # 照片辨識出的文字剛好有「加好友」不算。不唸語音、不寫進對話紀錄，
            # 比照貼圖（見 dispatcher._reply_to_sticker）。
            if (
                message_type == "text"
                and self._share_card_service is not None
                and is_share_intent(user_text)
            ):
                success = await self._replier.reply(
                    reply_token=reply_token,
                    message_text=await self._share_card_service.build_reply_text(),
                    user_id=user_id,
                    voice_reply_enabled=False,
                    language=user_language,
                )
                log_stage(logger, "share_card", ok=success)
                return

            # 看診錄音：整句就是要開始錄，直接回徵詢同意的那一則（理由同分享卡）。
            if (
                message_type == "text"
                and self._clinic_recording_flow is not None
                and is_clinic_recording_intent(user_text)
            ):
                await self._clinic_recording_flow.start(user_id, reply_token, user_language)
                return

            # 每輪開頭建立 holder：上一輪的來源殘留下來，會變成這一輪卡片上
            # 不屬於這個問題的來源按鈕。必須在 agent 執行之前、於這一層建立，
            # tool 才改得到同一個物件（見 app/core/rag_sources.py）。
            rag_sources_token = begin_request_rag_sources()

            self._schedule_loading_animation(user_id)

            t1 = time.perf_counter()
            line_user_token = set_line_user_id(user_id)
            try:
                # 總上限（來由見 config.AGENT_TOTAL_TIMEOUT_SECONDS）。agent 裡
                # 只有 RAG 那條腿有自己的逾時，Gemini 呼叫本身沒有；少了這層，
                # 一次掛住的呼叫會讓這位使用者的 per-user lock 永遠不放，之後
                # 的每一句都排在後面等。
                agent_response = await asyncio.wait_for(
                    self._agent.invoke(
                        user_input=user_text,
                        messages=chat_history,
                        user_profile=user_profile,
                    ),
                    timeout=settings.AGENT_TOTAL_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                log_stage(
                    logger,
                    "agent_timeout",
                    timeout_s=settings.AGENT_TOTAL_TIMEOUT_SECONDS,
                    ms=int((time.perf_counter() - t1) * 1000),
                )
                # 不存對話紀錄：這一輪沒有回答，存進去只會讓下一輪的 agent 看到
                # 一句沒被回應的問題。
                await self._replier.reply(
                    reply_token=reply_token,
                    message_text=t("line.fallback_busy", language=user_language),
                    user_id=user_id,
                    voice_reply_enabled=False,
                    language=user_language,
                )
                return
            finally:
                reset_line_user_id(line_user_token)
            log_stage(
                logger,
                "agent_done",
                ms=int((time.perf_counter() - t1) * 1000),
            )

            response_payload = agent_response.get("response")
            if not response_payload:
                logger.warning(
                    f"{LOGGER_HEADER_TEXT} agent 回覆為空，將使用預設純文字回覆，message_type=%s, user_id=%s",
                    message_type,
                    user_id,
                )
            else:
                logger.info(
                    f"{LOGGER_HEADER_TEXT} 已取得 agent 回覆，message_type=%s, response_type=%s",
                    message_type,
                    type(response_payload).__name__,
                )

            response_text = response_payload or t(
                "line.fallback_ununderstood", language=user_language
            )
            call_request_location = agent_response.get("call_request_location", False)

            # 判定為緊急時通報家人。刻意排在回覆之前排程、但不 await——當事人
            # 那張紅卡是最該先到的東西，查族譜與逐一推播全部串在前面就是讓
            # 正在出事的人多等好幾秒。
            if agent_response.get("emergency"):
                self._schedule_emergency_family_alert(
                    user_id,
                    agent_response.get("emergency_reason") or "",
                    user_language,
                    patient_words=user_text,
                )
            voice_reply_enabled = self._parse_voice_reply_enabled(user_profile)
            voice_rate = self._parse_voice_rate(user_profile)
            voice_gender = self._parse_voice_gender(user_profile)

            t2 = time.perf_counter()
            success = await self._replier.reply(
                reply_token=reply_token,
                message_text=response_text,
                user_id=user_id,
                request_location=call_request_location,
                voice_reply_enabled=voice_reply_enabled,
                language=user_language,
                voice_rate=voice_rate,
                voice_gender=voice_gender,
                answer_kind=agent_response.get("answer_kind"),
                user_question=user_text,
                # 緊急時紅卡要是第一則（理由同上方家人通報），表格卡會把它擠到第二則，不送。
                image_text="" if agent_response.get("emergency") else image_text,
                speech_language=language_choice,
                lost_help_postback=None if agent_response.get("emergency") else lost_help_postback,
            )
            log_stage(
                logger,
                "reply",
                ok=success,
                ms=int((time.perf_counter() - t2) * 1000),
            )

            if success:
                await self._history_service.save_turn(
                    user_id=user_id,
                    user_text=user_text,
                    ai_reply=response_text,
                    message_type=message_type,
                    event_time=event_time,
                )
            else:
                logger.error("Failed to reply to user %s", user_id)

        except Exception:
            logger.exception("Error in processing Line message event")
            await self._replier.reply(
                reply_token=reply_token,
                message_text=t("line.fallback_process_error", language=user_language),
                user_id=user_id,
                voice_reply_enabled=False,
                language=user_language,
            )
        finally:
            if lang_token is not None:
                reset_request_language(lang_token)
            if font_token is not None:
                reset_request_font_size(font_token)
            if age_token is not None:
                reset_request_age(age_token)
            if rag_sources_token is not None:
                reset_request_rag_sources(rag_sources_token)

    async def start_lost_flow(
        self,
        *,
        user_id: str,
        reply_token: str,
        user_text: str,
        intent: str,
        language: str,
        font_size: str,
    ) -> None:
        """回長輩定位卡，並在背景通知家人。

        卡片先回、通知在背景：推給每位家人各要一次 LINE API，長輩不該等它們跑完
        才看到按鈕。「家人已經收到通知」是推播真的送出之後才補的一則，理由同
        緊急通報（notify_patient_family_was_told）：卡片上不寫可能不成立的話。
        """
        service = self._lost_location_service
        report = await service.report(user_id, user_text, intent)

        if report.outcome == "no_family":
            card = build_no_family_flex(language=language, font_size=font_size)
        else:
            header_key = (
                "lost.elder.header.active"
                if report.outcome == "already_active"
                else f"lost.elder.header.{intent}"
            )
            card = service.elder_card(header_key, language, font_size)
        ok = await self._replier.reply_flex(
            reply_token=reply_token, flex_message=card, user_id=user_id
        )
        log_stage(logger, "lost", intent=intent, outcome=report.outcome, ok=ok)
        self._schedule_urgency_check_for_lost(user_id, user_text, language, font_size)

        if report.outcome != "started" or report.session is None:
            return
        session = report.session

        async def _notify() -> None:
            try:
                sent = await service.notify_family_of_report(session)
                key = "lost.elder.family_notified" if sent else "lost.elder.notify_failed"
                await self._replier.push_text(user_id, t(key, language))
            except Exception:
                logger.exception("走失通報任務失敗")

        task = asyncio.create_task(_notify())
        self._safety_alert_tasks.add(task)
        task.add_done_callback(self._safety_alert_tasks.discard)

    def _schedule_urgency_check_for_lost(
        self, user_id: str, user_text: str, language: str, font_size: str
    ) -> None:
        """走失流程沒進 agent，補跑急迫度判斷；緊急就補推紅卡並通報家人。

        為什麼需要：走失判斷會把少數急症當成走失（holdout 989 則急症中 1 則，
        「阮後生不知按怎叫袂醒身軀冷冰冰」），而走失流程不經過 agent 的紅卡。
        為什麼不先判急迫度再決定要不要走失流程：急迫度的本地模型沒看過走失的
        句子，「快來接我」這類求救多半落在沒把握區間、要等 Gemini，長輩的定位卡
        就得多等一次 API。放背景跑，定位卡照常秒回，紅卡晚幾秒到。
        """
        if self._urgency_classifier is None or not user_text:
            return

        async def _run() -> None:
            try:
                verdict = await self._urgency_classifier.classify(user_text, language=language)
                if not verdict.is_emergency:
                    return
                payload = build_emergency_condition_flex(
                    verdict, language=language, font_size=font_size
                )
                card = FlexMessage(
                    altText=payload["altText"],
                    contents=FlexContainer.from_dict(payload["contents"]),
                )
                ok = await self._replier.push_flex(user_id, card)
                log_stage(logger, "lost_emergency", ok=ok)
                self._schedule_emergency_family_alert(
                    user_id, verdict.display, language, patient_words=user_text
                )
            except Exception:
                logger.exception("走失流程的急迫度補判失敗")

        task = asyncio.create_task(_run())
        self._safety_alert_tasks.add(task)
        task.add_done_callback(self._safety_alert_tasks.discard)

    def _schedule_safety_alert_check(self, user_id: str, user_text: str) -> None:
        """把一次風險評估丟到背景執行。

        失敗一律留在任務內部：使用者沒有在等這個結果，讓例外逸散只會變成
        "Task exception was never retrieved"，而且會污染主流程的錯誤處理。
        """
        if self._safety_alert_service is None or not user_id or not user_text:
            return

        async def _run() -> None:
            try:
                await self._safety_alert_service.check(user_id, user_text)
            except Exception:  # noqa: BLE001 - 背景旁路，例外不得逸散
                logger.exception("用藥風險評估任務失敗")

        task = asyncio.create_task(_run())
        self._safety_alert_tasks.add(task)
        task.add_done_callback(self._safety_alert_tasks.discard)

    def _schedule_emergency_family_alert(
        self, user_id: str, reason: str, language: str, *, patient_words: str = ""
    ) -> None:
        """把一次家人通報丟到背景執行，與主回覆併行。

        通報成功後才回頭告訴當事人「家人已經知道了」——紅卡在通報之前就送出去
        了，組卡當下還不知道通報會不會成功，與其在卡上寫一句可能不成立的話，
        不如事後補一則（見 emergency_alert_service 的模組註解）。

        失敗一律留在任務內部，理由同 _schedule_safety_alert_check。
        """
        if self._emergency_family_alert_service is None or not user_id:
            return

        async def _run() -> None:
            try:
                sent = await self._emergency_family_alert_service.notify(
                    user_id, reason, patient_words
                )
                if sent:
                    await notify_patient_family_was_told(
                        self._replier, user_id, language
                    )
            except Exception:  # noqa: BLE001 - 背景旁路，例外不得逸散
                logger.exception("緊急狀況家人通報任務失敗")

        task = asyncio.create_task(_run())
        self._safety_alert_tasks.add(task)
        task.add_done_callback(self._safety_alert_tasks.discard)

    @staticmethod
    def _language_from_profile(user_profile: Optional[dict]) -> str:
        """文字語言（台語 → zh-TW）。"""
        return normalize_user_language(
            BaseLineMessageHandler._language_choice_from_profile(user_profile)
        )

    @staticmethod
    def _language_choice_from_profile(user_profile: Optional[dict]) -> str:
        """使用者選的語言，含只換語音的台語。"""
        settings = (user_profile or {}).get("settings") or {}
        return normalize_language_choice(settings.get("language"))

    @staticmethod
    def _font_size_from_profile(user_profile: Optional[dict]) -> str:
        settings = (user_profile or {}).get("settings") or {}
        return normalize_user_font_size(settings.get("font_size"))

    def _parse_voice_reply_enabled(self, user_profile: Optional[dict]) -> bool:
        """同步解析使用者個人檔案中的語音回覆設定；缺省為 False（與 UserSettings 及 LIFF UI 一致）。"""
        if not user_profile:
            return False
        settings_dict = user_profile.get("settings") or {}
        if "voice_reply_enabled" in settings_dict:
            return bool(settings_dict["voice_reply_enabled"])
        return bool(user_profile.get("voice_reply_enabled", False))

    def _parse_voice_rate(self, user_profile: Optional[dict]) -> str:
        """同步解析使用者個人檔案中的語速設定，缺值時預設為 "normal"。"""
        if not user_profile:
            return "normal"
        settings_dict = user_profile.get("settings") or {}
        if "voice_rate" in settings_dict:
            return settings_dict["voice_rate"] or "normal"
        return user_profile.get("voice_rate") or "normal"

    def _parse_voice_gender(self, user_profile: Optional[dict]) -> str:
        """同步解析使用者個人檔案中的語音性別設定，缺值時預設為 "female"。"""
        if not user_profile:
            return "female"
        settings_dict = user_profile.get("settings") or {}
        if "voice_gender" in settings_dict:
            return settings_dict["voice_gender"] or "female"
        return user_profile.get("voice_gender") or "female"


class LineMessageHandler(BaseLineMessageHandler):
    """處理文字訊息事件。"""

    async def handle(
        self, event: MessageEvent, *, user_profile: dict | None = None
    ) -> None:
        message = event.message
        if not isinstance(message, TextMessageContent):
            raise ValueError("Expected TextMessageContent")
        await self._process_and_reply(
            event, message.text, "text", user_profile=user_profile
        )
