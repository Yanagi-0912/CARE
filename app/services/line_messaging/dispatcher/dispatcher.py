import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional
from urllib.parse import parse_qs

from linebot.v3.webhooks import (
    AudioMessageContent,
    FileMessageContent,
    FollowEvent,
    ImageMessageContent,
    LocationMessageContent,
    MessageEvent,
    PostbackEvent,
    StickerMessageContent,
    TextMessageContent,
    UnfollowEvent,
    UserSource,
    VideoMessageContent,
)
from app.core.request_context import (
    new_request_id,
    reset_request_id,
    set_request_id,
)
from app.core.user_font_size import (
    normalize_user_font_size,
    reset_request_font_size,
    set_request_font_size,
)
from app.core.user_age import reset_request_age, set_request_age
from app.core.user_language import (
    DEFAULT_USER_LANGUAGE,
    normalize_user_language,
    reset_request_language,
    set_request_language,
)
from app.core.request_logging import log_done, log_stage, log_start
from app.i18n.messages import t
from resources.flex_messages.lost_location_flex_message import LOST_CONFIRM_ACTION
from app.models.medication import to_taipei_hm
from app.services.appointment.appointment_service import AppointmentError
from app.services.line_messaging.flex.appointment_flex import (
    ATTEND_ACTION,
    DEPART_ACTION,
    build_report_ack_flex,
    format_hm,
    format_when,
)
from app.services.line_messaging.flex.medication_flex import build_patient_medication_flex
from app.services.line_messaging.flex.welcome_flex import build_welcome_flex
from app.services.line_messaging.handler.message_handler import (
    LineMessageHandler,
    LineValidationError,
)
from app.services.line_messaging.handler.facility_detail_handler import LineFacilityDetailHandler
from app.services.line_messaging.handler.media_handler import LineMediaHandler
from app.services.line_messaging.handler.location_handler import LineLocationHandler
from app.services.line_messaging.reply.reply import LineReplier
from app.services.line_messaging.sticker_reply import sticker_reply_key
from app.repositories.user_profile_repository import UserProfileRepository

logger = logging.getLogger(__name__)

# 追蹤狀態 (line_id, following, at) → 是否有更新到。預設走 repository 的
# 靜態方法；測試以建構子注入替身。
SetFollowingFn = Callable[[str, bool, datetime], Awaitable[bool]]


def _event_label(event) -> str:
    if isinstance(event, PostbackEvent):
        return "postback"
    if isinstance(event, MessageEvent):
        message = event.message
        if isinstance(message, TextMessageContent):
            return "text"
        if isinstance(message, LocationMessageContent):
            return "location"
        if isinstance(message, StickerMessageContent):
            return "sticker"
        if isinstance(
            message,
            (
                ImageMessageContent,
                VideoMessageContent,
                AudioMessageContent,
                FileMessageContent,
            ),
        ):
            return getattr(message, "type", None) or type(message).__name__
        return f"message:{type(message).__name__}"
    return type(event).__name__


class LineEventDispatcher:
    """事件分發器，負責接收 Webhook 解析的事件，並分發至對應處理器。"""

    def __init__(
        self,
        message_handler: LineMessageHandler,
        media_handler: LineMediaHandler,
        location_handler: LineLocationHandler,
        facility_detail_handler: LineFacilityDetailHandler,
        replier: LineReplier,
        medication_service=None,
        medical_news_share_service=None,
        appointment_service=None,
        line_language_service=None,
        liff_url: str = "",
        set_following: Optional[SetFollowingFn] = None,
    ):
        self._message_handler = message_handler
        self._media_handler = media_handler
        self._location_handler = location_handler
        self._facility_detail_handler = facility_detail_handler
        self._replier = replier
        self._medication_service = medication_service
        # 未設定時該 postback 分支只記 log，與 _medication_service 為 None 時的
        # 既有處理一致——功能沒開不該讓事件處理拋錯。
        self._medical_news_share_service = medical_news_share_service
        # 掛號提醒卡片上的「我已出發／我已到診」。未設定時同樣只記 log。
        self._appointment_service = appointment_service
        # 加好友歡迎卡：新好友還沒有 profile，語言改向 LINE 查。未設定時用預設
        # 語言；liff_url 為空時卡片省略填資料按鈕。
        self._line_language_service = line_language_service
        self._liff_url = liff_url
        self._set_following = set_following
        # 同一位使用者的事件要照順序處理：webhook 現在是每個事件各開一個 task
        # （見 routers/line/webhook.py），同一個人連傳兩句會併行，第二句的
        # agent 讀不到第一句的對話紀錄，回覆順序也可能顛倒。不同使用者之間
        # 仍然併行。dict 以使用者為鍵，值是 (lock, 等待中的事件數)；沒人在
        # 用就移除，所以大小上限是「此刻正在處理的使用者數」，不會無限長。
        self._user_locks: dict[str, list] = {}

    async def handle(self, event) -> None:
        """分發單一事件至對應的方法處理。"""
        source = getattr(event, "source", None)
        if not isinstance(source, UserSource):
            # 群組／聊天室（GroupSource／RoomSource）的事件：CARE 是一對一的
            # 健康助理，個人檔案、對話紀錄、家人通報都綁在個人上，群組裡的
            # 訊息不能拿某個人的檔案去回。只記 info、不回覆。
            logger.info(
                "略過非一對一來源的 LINE 事件 source=%s event=%s",
                getattr(source, "type", None) or type(source).__name__,
                type(event).__name__,
            )
            return

        user_id = getattr(source, "user_id", "") or ""
        if not user_id:
            logger.warning("LINE 事件缺少 user_id，無法處理 event=%s", type(event).__name__)
            return

        # reply_token 只有 message／postback／follow 這類事件才有；unfollow 沒有，
        # 那不是格式錯誤。缺 token 的可回覆事件照常處理：replier 會自動改走 push。
        reply_token = getattr(event, "reply_token", "") or ""
        if not reply_token and not isinstance(event, UnfollowEvent):
            logger.warning(
                "LINE 事件缺少 reply_token，回覆將改走 push event=%s", type(event).__name__
            )

        rid_token = set_request_id(new_request_id())
        started = time.perf_counter()
        status = "ok"
        event_type = type(event).__name__
        handler = getattr(self, f"_handle_{event_type}", self._handle_unsupported_event)

        # 語言在進 handler 之前就決定好：以前是在 except 裡才查，處理失敗時
        # 再查一次 Mongo——如果失敗的原因正是 Mongo 掛了，這一查會在 except
        # 裡再炸一次，使用者連「發生錯誤」都收不到。
        user_language = await self._resolve_user_language_safely(user_id)

        try:
            log_start(
                logger,
                event=_event_label(event),
                user=user_id[:10],
            )
            async with self._user_lock(user_id):
                await handler(event)
        except LineValidationError as e:
            status = "validation_error"
            await self._reply_error_safely(reply_token, user_id, str(e), user_language)
        except Exception:
            status = "error"
            logger.exception("Error in event dispatcher handling event %s", event_type)
            await self._reply_error_safely(
                reply_token,
                user_id,
                t("line.fallback_process_error", language=user_language),
                user_language,
            )
        finally:
            total_ms = int((time.perf_counter() - started) * 1000)
            log_done(logger, status=status, total_ms=total_ms)
            reset_request_id(rid_token)

    @asynccontextmanager
    async def _user_lock(self, user_id: str):
        entry = self._user_locks.setdefault(user_id, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] <= 0 and self._user_locks.get(user_id) is entry:
                del self._user_locks[user_id]

    async def _resolve_user_language_safely(self, user_id: str) -> str:
        try:
            return await self._resolve_user_language(user_id)
        except Exception:
            logger.warning("讀取使用者語言失敗，以預設語言回覆", exc_info=True)
            return DEFAULT_USER_LANGUAGE

    async def _reply_error_safely(
        self, reply_token: str, user_id: str, text: str, language: str
    ) -> None:
        """except 分支裡的回覆：這裡再拋出去就沒有人接了（webhook 已回 200）。"""
        try:
            await self._replier.reply(
                reply_token=reply_token,
                message_text=text,
                user_id=user_id,
                voice_reply_enabled=False,
                language=language,
            )
        except Exception:
            logger.exception("錯誤說明也送不出去 user_id=%s", user_id[:10])

    async def _handle_MessageEvent(self, event: MessageEvent) -> None:
        message = event.message

        if isinstance(message, TextMessageContent):
            await self._message_handler.handle(event)
        elif isinstance(message, LocationMessageContent):
            await self._location_handler.handle(event)
        elif isinstance(
            message,
            (
                ImageMessageContent,
                VideoMessageContent,
                AudioMessageContent,
                FileMessageContent,
            ),
        ):
            await self._media_handler.handle(event)
        elif isinstance(message, StickerMessageContent):
            await self._reply_to_sticker(event, message)
        else:
            logger.warning("Unsupported message content type: %s", type(message).__name__)

    async def _reply_to_sticker(
        self, event: MessageEvent, message: StickerMessageContent
    ) -> None:
        """貼圖回一句固定的話，不進 agent（理由見 sticker_reply 模組說明）。

        不唸語音、不寫進對話紀錄：跟按鈕回覆的「已記錄」一樣只是一句應答；對話
        紀錄只帶最近五則進 agent，貼圖寫進去會把前面真正的提問擠出去。
        """
        user_id = getattr(event.source, "user_id", "")
        key = sticker_reply_key(message.keywords, message.text)
        # 關鍵字是實驗性欄位：記下每張貼圖帶了幾個、落在哪一類，才看得出
        # fallback 的比例高不高、值不值得改成讓模型看圖。
        log_stage(
            logger,
            "sticker",
            reply=key,
            keywords=len(message.keywords or []),
            has_text=bool(message.text),
        )
        language = await self._resolve_user_language(user_id)
        await self._replier.reply(
            reply_token=event.reply_token,
            message_text=t(key, language=language),
            user_id=user_id,
            voice_reply_enabled=False,
            language=language,
        )

    async def _handle_PostbackEvent(self, event: PostbackEvent) -> None:
        user_id = getattr(event.source, "user_id", "")
        user_profile = None
        if self._user_profile_service:
            user_profile = await self._user_profile_service.get_user_profile(user_id)

        # 下游 handler 只拿得到 user_id，語言與字級改由 ContextVar 傳遞
        lang_token = set_request_language(self._language_from_profile(user_profile))
        font_token = set_request_font_size(self._font_size_from_profile(user_profile))
        age_token = set_request_age((user_profile or {}).get("age"))
        try:
            await self._dispatch_postback(event, user_id, user_profile)
        finally:
            reset_request_language(lang_token)
            reset_request_font_size(font_token)
            reset_request_age(age_token)

    async def _dispatch_postback(
        self, event: PostbackEvent, user_id: str, user_profile
    ) -> None:
        reply_token = getattr(event, "reply_token", "")
        postback_data = getattr(getattr(event, "postback", None), "data", "")
        user_language = self._language_from_profile(user_profile)

        params = parse_qs(postback_data)
        action = params.get("action", [""])[0]

        if action == "confirm_medication":
            log_id = params.get("log_id", [""])[0]
            if not log_id:
                logger.warning("confirm_medication postback missing log_id")
                return

            if self._medication_service:
                # 逐藥確認的 postback 才會帶 medication_id；整批的【全部已服用】
                # 不帶，維持既有行為（spec「逐藥確認」：不帶藥品 id 的確認直接
                # 轉 taken）。
                medication_id = params.get("medication_id", [""])[0] or None
                log = await self._medication_service.confirm_medication(
                    log_id, user_id, medication_id=medication_id
                )

                if log.status == "taken":
                    taken_time_str = to_taipei_hm(log.taken_at)
                    scheduled_time_str = to_taipei_hm(log.scheduled_at, default="08:00")
                    # 已完成的卡片要留下「這次吃了哪幾種藥」——提醒卡上有的資訊
                    # 不該在按下確認後就消失，那是使用者事後唯一查得到的憑據。
                    # 查不到時回傳空清單，卡片自動退回沒有藥品區塊的原樣。
                    medication_names = (
                        await self._medication_service.list_medication_names_for_log(log)
                    )

                    disabled_flex = build_patient_medication_flex(
                        log_id=log_id,
                        slot_type=log.slot_type,
                        scheduled_time=scheduled_time_str,
                        disabled=True,
                        taken_at_str=taken_time_str,
                        medication_names=medication_names,
                        language=user_language,
                        font_size=self._font_size_from_profile(user_profile),
                    )
                    await self._replier.reply_flex(
                        reply_token=reply_token,
                        flex_message=disabled_flex,
                        user_id=user_id,
                    )
                else:
                    # 逐藥確認尚未到齊：以純文字回覆已記錄與尚未確認的藥品，
                    # 讓使用者知道「按有生效」而不必等下一則卡片（spec
                    # 「逐藥確認」，design 決策 6）。回覆走 reply token，
                    # 不耗推播額度。
                    taken = await self._medication_service.taken_names_for_log(log)
                    remaining = [
                        entry.name
                        for group in await self._medication_service.medication_groups_for_log(
                            log
                        )
                        for (_medication_id, entry) in group.items
                    ]
                    # `taken_names_for_log` 查不到藥名時退化回空清單（見該
                    # 方法註解：查詢失敗只記 log、不往外拋）——這裡不能照樣
                    # `、`.join 出一段空字串塞進「已記錄：」後面，那會回覆
                    # 「已記錄：。還有 N 種：…」這種看不出記錄了什麼的句子。
                    # 四種組合分開處理：有記錄／無記錄各自搭配有／無待確認，
                    # 都沒有時才退回 meds.recorded 的既有措辭。
                    if taken and remaining:
                        progress_text = t(
                            "meds.progress", language=user_language
                        ).format(
                            taken="、".join(taken),
                            count=len(remaining),
                            remaining="、".join(remaining),
                        )
                    elif taken:
                        progress_text = t(
                            "meds.progress_none_left", language=user_language
                        ).format(taken="、".join(taken))
                    elif remaining:
                        progress_text = t(
                            "meds.progress_no_taken", language=user_language
                        ).format(
                            count=len(remaining),
                            remaining="、".join(remaining),
                        )
                    else:
                        progress_text = t("meds.recorded", language=user_language)
                    await self._replier.reply(
                        reply_token=reply_token,
                        message_text=progress_text,
                        user_id=user_id,
                        voice_reply_enabled=False,
                        language=user_language,
                    )
            else:
                await self._replier.reply(
                    reply_token=reply_token,
                    message_text=t("meds.recorded", language=user_language),
                    user_id=user_id,
                    voice_reply_enabled=False,
                    language=user_language,
                )
        elif action in (DEPART_ACTION, ATTEND_ACTION):
            await self._handle_appointment_report(
                action=action,
                appointment_id=params.get("appointment_id", [""])[0],
                reply_token=reply_token,
                user_id=user_id,
                user_profile=user_profile,
            )
        elif action == "share_medical_news":
            news_ref = params.get("news_ref", [""])[0]
            if not news_ref:
                logger.warning("share_medical_news postback missing news_ref")
                return
            if self._medical_news_share_service is None:
                logger.warning("share_medical_news postback but service not configured")
                return
            await self._medical_news_share_service.share(
                sharer_id=user_id,
                news_ref=news_ref,
                reply_token=reply_token,
                language=user_language,
                font_size=self._font_size_from_profile(user_profile),
            )
        elif action == LOST_CONFIRM_ACTION:
            # 走失分類器沒把握時，回覆下方多一顆「我迷路了，通知家人」，他按了。
            # 原話從 postback 帶回來，家人收到的通報才有他當時說的話。
            await self._message_handler.start_lost_flow(
                user_id=user_id,
                reply_token=reply_token,
                user_text=params.get("w", [""])[0],
                intent="lost",
                language=user_language,
                font_size=self._font_size_from_profile(user_profile),
            )
        elif action == "already_done":
            await self._replier.reply(
                reply_token=reply_token,
                message_text=t("meds.already_recorded", language=user_language),
                user_id=user_id,
                voice_reply_enabled=False,
                language=user_language,
            )
        elif action == "toggle_voice_reply":
            if "enabled" in params:
                enabled = params.get("enabled", ["false"])[0].lower() == "true"
            else:
                current = self._parse_voice_reply_enabled(user_profile)
                enabled = not current
            updated = False
            if self._user_profile_service:
                updated = await self._user_profile_service.update_voice_reply_enabled(
                    user_id, enabled
                )

            if updated:
                status_msg = (
                    t("voice.enabled", language=user_language)
                    if enabled
                    else t("voice.disabled", language=user_language)
                )
            else:
                status_msg = t("voice.need_login", language=user_language)
            await self._replier.reply(
                reply_token=reply_token,
                message_text=status_msg,
                user_id=user_id,
                voice_reply_enabled=False,
                language=user_language,
            )
        #點擊"查看院所詳細資訊"按鈕時，回應該診所的詳細資料
        elif action == "view_facility_detail":
            facility_id = params.get("facility_id", [""])[0]
            await self._facility_detail_handler.handle_view_facility_detail(
                facility_id=facility_id,
                reply_token=reply_token,
                user_id=user_id,
            )
        else:
            logger.warning("Unknown postback action: %s", action)

    async def _handle_appointment_report(
        self,
        *,
        action: str,
        appointment_id: str,
        reply_token: str,
        user_id: str,
        user_profile,
    ) -> None:
        """掛號提醒卡片上的「我已出發／我已到診」。

        本人與家屬收到同一張卡、同一顆按鈕，按下的人就是回報者。授權與狀態轉移
        都在 AppointmentService——與 LIFF 的 POST .../depart、.../attend 同一條路——
        這裡只負責把結果換成回覆。

        LINE 不能修改已送出的訊息：原本那張卡片上的按鈕之後仍然按得下去。所以回覆
        的是一張新的「已記錄」卡片；之後有人再按同一顆按鈕（例如另一位家屬），服務層
        冪等回傳現況，這裡回覆同一張「已記錄」卡片，上面寫著原本是誰在幾點回報的。
        """
        if not appointment_id:
            logger.warning("%s postback missing appointment_id", action)
            return
        if self._appointment_service is None:
            logger.warning("%s postback but appointment service not configured", action)
            return

        language = self._language_from_profile(user_profile)
        font_size = self._font_size_from_profile(user_profile)
        service = self._appointment_service
        try:
            if action == DEPART_ACTION:
                reminder = await service.depart(appointment_id, user_id)
            else:
                reminder = await service.attend(appointment_id, user_id)
        except AppointmentError as exc:
            await self._replier.reply(
                reply_token=reply_token,
                message_text=exc.localized(language),
                user_id=user_id,
                voice_reply_enabled=False,
                language=language,
            )
            return

        kind = "departed" if action == DEPART_ACTION else "attended"
        reported_at = reminder.departed_at if kind == "departed" else reminder.attended_at
        reporter_id = (
            reminder.departed_by_user_id if kind == "departed" else reminder.attended_by_user_id
        )
        fallback_name = t("flex.appt.fallback_name", language=language)
        if reporter_id == user_id:
            reporter_name = t("flex.appt.you", language=language)
        else:
            reporter_name = await service.display_name(reporter_id) or fallback_name
        patient_name = None
        if reminder.user_id != user_id:
            patient_name = await service.display_name(reminder.user_id) or fallback_name

        ack = build_report_ack_flex(
            kind=kind,
            when_text=format_when(reminder.local_appointment_at, language),
            hospital_name=reminder.hospital_name,
            reported_time=format_hm(reminder.local(reported_at)) if reported_at else "",
            reporter_name=reporter_name,
            patient_name=patient_name,
            language=language,
            font_size=font_size,
        )
        await self._replier.reply_flex(
            reply_token=reply_token,
            flex_message=ack,
            user_id=user_id,
        )

    async def _handle_FollowEvent(self, event: FollowEvent) -> None:
        """加好友（含解除封鎖後加回）時回一張歡迎卡。

        任何失敗都只記 log、不往外拋：外層 handle() 的例外處理會回「處理訊息時
        發生錯誤」，那不該是新好友收到的第一則訊息。
        """
        user_id = getattr(event.source, "user_id", "")
        # 封鎖後再加回：先前 unfollow 標成不再追蹤，這裡要標回來，排程器才會
        # 再推播給他。標記失敗不擋歡迎卡。
        await self._mark_following(user_id, True)
        try:
            language, font_size = await self._welcome_preferences(user_id)
            await self._replier.reply_flex(
                reply_token=event.reply_token,
                flex_message=build_welcome_flex(
                    self._liff_url, language=language, font_size=font_size
                ),
                user_id=user_id,
            )
        except Exception:
            logger.exception("Failed to send welcome card")

    async def _handle_UnfollowEvent(self, event: UnfollowEvent) -> None:
        """封鎖或刪除好友：只把 profile 標成不再追蹤，不回覆（也沒有 reply token）。

        不刪資料：LINE 的封鎖是可逆的，解除封鎖會再收到 FollowEvent；而且家人
        族譜、用藥紀錄還掛在這個人身上。標記是給排程器用的——推播給封鎖我們的
        人會被 LINE 拒收（400），白白吃額度還算成失敗。
        """
        user_id = getattr(event.source, "user_id", "")
        updated = await self._mark_following(user_id, False)
        log_stage(logger, "unfollow", updated=updated)

    async def _mark_following(self, user_id: str, following: bool) -> bool:
        """更新追蹤旗標；沒有 profile（從沒開過 LIFF）就沒東西可標，回 False。"""
        if not user_id:
            return False
        set_following = self._set_following or UserProfileRepository.set_following
        try:
            return bool(
                await set_following(user_id, following, datetime.now(tz=timezone.utc))
            )
        except Exception:
            logger.exception(
                "更新追蹤狀態失敗 user_id=%s following=%s", user_id[:10], following
            )
            return False

    async def _welcome_preferences(self, user_id: str) -> tuple[str, str]:
        """歡迎卡的語言與字級。

        已有 profile（封鎖後再加回）照使用者的設定；沒有的話向 LINE 查 App 語言，
        規則與 LIFF 首次登入相同——不在支援清單內就退回預設語言。
        """
        profile = None
        if self._user_profile_service:
            profile = await self._user_profile_service.get_user_profile(user_id)
        if profile:
            return self._language_from_profile(profile), self._font_size_from_profile(profile)

        line_language = None
        if self._line_language_service:
            # get_language 是同步 requests，丟到 thread 避免卡住 event loop
            line_language = await asyncio.to_thread(
                self._line_language_service.get_language, user_id
            )
        return normalize_user_language(line_language), normalize_user_font_size(None)

    async def _handle_unsupported_event(self, event) -> None:
        logger.warning("Unsupported LINE event type: %s", type(event).__name__)

    async def _resolve_user_language(self, user_id: str) -> str:
        if not self._user_profile_service:
            return DEFAULT_USER_LANGUAGE
        profile = await self._user_profile_service.get_user_profile(user_id)
        return self._language_from_profile(profile)

    @staticmethod
    def _language_from_profile(user_profile) -> str:
        if not user_profile:
            return DEFAULT_USER_LANGUAGE
        settings = user_profile.get("settings") or {}
        return normalize_user_language(settings.get("language"))

    @staticmethod
    def _font_size_from_profile(user_profile) -> str:
        settings = (user_profile or {}).get("settings") or {}
        return normalize_user_font_size(settings.get("font_size"))

    @staticmethod
    def _parse_voice_reply_enabled(user_profile) -> bool:
        """解析 profile 的語音回覆開關；缺省為 False（與 UserSettings 一致）。"""
        if not user_profile:
            return False
        settings = user_profile.get("settings") or {}
        if isinstance(settings, dict) and "voice_reply_enabled" in settings:
            return bool(settings["voice_reply_enabled"])
        return bool(user_profile.get("voice_reply_enabled", False))

    # --- 以下屬性為回溯相容與測試相容所設 ---

    @property
    def _agent(self):
        return self._message_handler._agent

    @property
    def _token_manager(self):
        return self._replier._token_manager

    @property
    def _user_profile_service(self):
        return self._message_handler._user_profile_service

    @property
    def _history_service(self):
        return self._message_handler._history_service

    @property
    def _tts_service(self):
        return self._replier._tts_service
