import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Awaitable, Callable, NamedTuple, Optional

from app.core import scheduler_heartbeat
from app.core.user_font_size import (
    DEFAULT_USER_FONT_SIZE,
    normalize_user_font_size,
)
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language
from app.models.medication import (
    TAIPEI_TZ,
    Medication,
    MedicationLog,
    MedicationReminder,
    ensure_aware_utc,
    to_taipei_hm,
)
from app.repositories.medication_repository import (
    MedicationLogRepository,
    MedicationReminderRepository,
    MedicationRepository,
)
from app.services.line_messaging.flex.medication_flex import (
    MedicationListEntry,
    build_caregiver_alert_flex,
    build_caregiver_missed_summary_flex,
    build_patient_medication_flex,
    build_patient_urgent_reminder_flex,
)
from app.services.line_messaging.reply.reply import LineReplier
from app.services.medication.drug_appearance_image_service import (
    resolve_drug_appearance_image_url,
)
from app.services.users.user_profile_service import UserProfileService

logger = logging.getLogger(__name__)

# 心跳登記名稱。用藥提醒是每 60 秒一輪的短週期排程，因此它的心跳是判斷
# 「排程器 pod 是否還健康」最靈敏的訊號——每日諮詢摘要睡到隔天才醒，
# 停擺一整天都還在它的正常範圍內，當不了 liveness 依據。
HEARTBEAT_NAME = "medication"

# 錯過多久之後就不再補推播。對應 APScheduler 的 misfire_grace_time。
# 預設取 20 分鐘（＝T+20 催促的門檻）：短暫部署造成的延遲仍會正常送達，
# 超過這個範圍代表整條 T+0／T+20／T+30 時序已經失去意義，補推只會變成連環轟炸。
DEFAULT_MISFIRE_GRACE_MINUTES = 20


class _RecipientPrefs(NamedTuple):
    """一位收件人的呈現偏好與通知意願。

    `notify_reminder` 與 `notify_family` 在 UserSettings 與 LIFF 設定頁上存在
    已久，但後端從來沒有讀過它們——使用者把開關關掉，推播照送。UI 對使用者
    說謊，而這件事不報錯、不留 log，只表現為「我明明關了還是一直收到」，
    使用者多半會歸咎於自己按錯或直接封鎖官方帳號。這個型別是把它們真正接上
    的那一步。
    """

    language: str
    font_size: str
    notify_reminder: bool
    notify_family: bool


class _TickMedicationNameCache:
    """一個 tick 內、同一階段（T+0／T+20／T+30）所有待推播 log 共用的藥品清單查表。

    背景：同一時段（例如每天 08:00）常常有多位使用者共用，若每筆 log 各自查一次
    「規則→藥品」，一個 tick 就會是 2 x（待推播 log 數）次序列往返；改成整批查詢後
    不論 log 有幾筆，固定只發生「查規則」與「查藥品」各一次（同一批 log 若跨到不同
    台北日期，藥品查詢會依日期分組各發一次，但這在實務上幾乎不會發生——誤點超過
    misfire grace 的 log 在建立當下就已被靜默為 missed，不會進到這裡）。

    刻意延遲到第一次真正要組裝文案時（也就是第一筆 log 的 claim 成功之後）才發出
    查詢：`get()`／`get_entries()` 只會被 `_send_patient_reminder`／
    `_send_urgent_reminder`／`_send_caregiver_alert` 呼叫，而它們只在 `_dispatch`
    搶到推播權之後才會執行。這保證「claim 必須先於任何藥品查詢」永遠成立——查詢
    的時機不會提前到迴圈裡第一筆 log 的 claim 之前，之後的 log 讀的是已經查好的
    結果，不會再發任何新查詢，也就不會因為查詢延遲或失敗而影響到任何一筆 log 的
    搶佔時機。

    以 `log.id` 而非 `reminder_id` 當查表的 key：同一個 reminder 理論上可能同時有
    跨日的兩筆 pending log（例如停機補建），用 reminder_id 當 key 會讓其中一筆的
    查詢結果覆蓋掉另一筆，用 log.id 可以完全避免這個邊界情況。

    縮圖 URL 的解析沿用這裡「查規則→查藥品」同一批結果，不是另外的查詢——
    `_load()` 在組出 `entry_by_id` 的同一個迴圈裡，順便對每筆藥品解析縮圖，因此
    無論 log 數多寡，縮圖解析也只發生在這固定一批藥品上，不會隨 log 數量增加。
    T+0／T+20 用藥者提醒需要縮圖（spec「推播的藥品清單得帶出藥丸縮圖」），T+30
    家屬警報不需要（spec「家屬卡片不含縮圖」）：兩者共用同一份查表，差別只在
    讀出來時要不要保留 image_url——`get()` 只回藥名（給家屬警報用），
    `get_entries()` 回帶縮圖 URL 的完整列（給用藥者提醒用）。
    """

    def __init__(
        self,
        logs: list["MedicationLog"],
        *,
        resolve_image_url: Callable[[str], Optional[str]] = resolve_drug_appearance_image_url,
        reminder_repository=MedicationReminderRepository,
        medication_repository=MedicationRepository,
    ) -> None:
        self._logs = logs
        # 可注入的縮圖解析函式：正式路徑用真正的檔案系統查詢，測試可以換一個
        # 會丟例外的假函式，驗證「縮圖解析失敗不能讓推播跟著噴掉」
        # （見 `_resolve_thumbnail`）。
        self._resolve_image_url = resolve_image_url
        # 可注入的 repository：預設就是真正的那兩個 class（它們的方法都是
        # staticmethod，傳 class 本身即可當成物件用）。測試據此餵假的
        # repository，不必用 monkeypatch 換掉別處 import 進來的名稱——
        # openspec 的測試規則明文禁止後者，慣例與 MedicationService 一致。
        self._reminder_repository = reminder_repository
        self._medication_repository = medication_repository
        self._entries_by_log_id: Optional[dict[str, list[MedicationListEntry]]] = None

    async def get(
        self,
        log: "MedicationLog",
        *,
        reminder_collection: Optional[Any] = None,
        medication_collection: Optional[Any] = None,
    ) -> list[str]:
        """取得指定 log 的藥名清單（不含縮圖）；供家屬警報使用。

        `reminder_collection`／`medication_collection` 僅供測試注入假的
        collection；正式路徑一律傳 None，落回 MongoDBManager 的真實連線。
        """
        entries = await self.get_entries(
            log,
            reminder_collection=reminder_collection,
            medication_collection=medication_collection,
        )
        return [entry.name for entry in entries]

    async def get_entries(
        self,
        log: "MedicationLog",
        *,
        reminder_collection: Optional[Any] = None,
        medication_collection: Optional[Any] = None,
    ) -> list[MedicationListEntry]:
        """取得指定 log 的藥品清單列（藥名＋縮圖 URL）；供用藥者提醒使用。

        第一次呼叫（不論是這支或 `get`）才會真的發出查詢，之後都讀已經查好的
        結果，理由見 class docstring。
        """
        if self._entries_by_log_id is None:
            self._entries_by_log_id = await self._load(
                reminder_collection=reminder_collection,
                medication_collection=medication_collection,
            )
        return self._entries_by_log_id.get(log.id, [])

    def _resolve_thumbnail(self, medication: Medication) -> Optional[str]:
        """證號已確定時才嘗試解析縮圖 URL。

        `license_number` 為空 SHALL NOT 顯示照片（spec「證號不確定時不得顯示藥丸
        照片」）——排程器只在這裡呼叫縮圖解析，把關必須設在這裡，不能指望
        `resolve_image_url` 自己判斷「這個證號是不是已經確定」，它只認檔案存不
        存在。

        解析本身出例外（例如測試注入的假解析器、或未來實作換成其他來源）不能讓
        整批藥品清單查詢連坐失敗；退化成沒有縮圖，文字列照常呈現、推播照常送出
        （spec「照片缺席時的降級」）。
        """
        if not medication.license_number:
            return None
        try:
            return self._resolve_image_url(medication.license_number)
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to resolve drug appearance thumbnail "
                "for medication %s",
                medication.id,
            )
            return None

    async def _load(
        self,
        *,
        reminder_collection: Optional[Any],
        medication_collection: Optional[Any],
    ) -> dict[str, list[MedicationListEntry]]:
        reminder_ids = sorted({log.reminder_id for log in self._logs})
        if not reminder_ids:
            return {}

        try:
            reminders = await self._reminder_repository.find_by_ids(
                reminder_ids, collection=reminder_collection
            )
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to batch-load reminders for medication list"
            )
            return {}
        reminder_by_id = {reminder.id: reminder for reminder in reminders}

        medication_ids = sorted(
            {mid for reminder in reminders for mid in reminder.medication_ids}
        )
        if not medication_ids:
            return {log.id: [] for log in self._logs}

        # 同一批 log 幾乎都落在同一天，但仍照每筆 log 自己的台北日期分組查詢，
        # 不用「這次 tick 的日期」概括所有 log，避免日期跨界時算錯藥品有效性
        # （理由同 class docstring：這種跨日情況雖然罕見，但不能因為批次化就犧牲
        # 既有的「用推播當下、而非規則建立當下」的有效性判斷）。
        logs_by_date: dict[str, list["MedicationLog"]] = {}
        for log in self._logs:
            date_str = (
                ensure_aware_utc(log.scheduled_at).astimezone(TAIPEI_TZ).strftime("%Y-%m-%d")
            )
            logs_by_date.setdefault(date_str, []).append(log)

        entries_by_log_id: dict[str, list[MedicationListEntry]] = {}
        for date_str, logs_on_date in logs_by_date.items():
            try:
                medications = await self._medication_repository.find_active_by_ids(
                    medication_ids, date_str, collection=medication_collection
                )
            except Exception:
                logger.exception(
                    "[MedicationScheduler] Failed to batch-load medications for medication list"
                )
                for log in logs_on_date:
                    entries_by_log_id[log.id] = []
                continue

            entry_by_id = {
                medication.id: MedicationListEntry(
                    name=medication.name,
                    image_url=self._resolve_thumbnail(medication),
                )
                for medication in medications
            }
            for log in logs_on_date:
                reminder = reminder_by_id.get(log.reminder_id)
                if not reminder:
                    entries_by_log_id[log.id] = []
                    continue
                entries_by_log_id[log.id] = [
                    entry_by_id[mid] for mid in reminder.medication_ids if mid in entry_by_id
                ]
        return entries_by_log_id


class MedicationScheduler:
    """
    雙階遞進定時排程引擎 (MedicationScheduler)
    1. T+0min  首刷提醒建立與推播
    2. T+20min 第二次溫馨催促推播 (若逾時未用藥)
    3. T+30min 第三次家屬逾時通報警報 (若仍未用藥)
    """

    def __init__(
        self,
        replier: LineReplier,
        user_profile_service: Optional[UserProfileService] = None,
        check_interval_seconds: int = 60,
        misfire_grace_minutes: int = DEFAULT_MISFIRE_GRACE_MINUTES,
        reminder_repository=MedicationReminderRepository,
        log_repository=MedicationLogRepository,
        medication_repository=MedicationRepository,
    ) -> None:
        self._replier = replier
        self._user_profile_service = user_profile_service
        self._check_interval_seconds = check_interval_seconds
        self._misfire_grace_minutes = misfire_grace_minutes
        # 三個 repository 全部走注入，預設就是真正的那三個 class（方法皆為
        # staticmethod，傳 class 本身即可當成物件用），因此正式路徑的行為與
        # 注入前完全相同。開這個縫是為了讓測試餵假的 repository，不必用
        # monkeypatch 換掉本模組 import 進來的名稱——openspec 的測試規則明文
        # 禁止後者，慣例與 MedicationService 一致。
        self._reminder_repository = reminder_repository
        self._log_repository = log_repository
        self._medication_repository = medication_repository
        self._task: Optional[asyncio.Task] = None

    def _medication_cache(self, logs: list[MedicationLog]) -> _TickMedicationNameCache:
        """建立一個階段共用的藥名查表，並把注入的 repository 帶下去。

        每個階段建立一次、在該階段的迴圈之外——在迴圈裡逐筆建立會讓批次化
        形同虛設，因為每個物件只服務一筆 log。
        """
        return _TickMedicationNameCache(
            logs,
            reminder_repository=self._reminder_repository,
            medication_repository=self._medication_repository,
        )

    async def _resolve_display_prefs(self, user_id: str) -> tuple[str, str]:
        """
        取得收件人的語言與字級設定。
        排程是背景工作，沒有 request context，因此每則推播都需按收件人各自解析。
        """
        prefs = await self._resolve_prefs(user_id)
        return prefs.language, prefs.font_size

    async def _resolve_prefs(self, user_id: str) -> "_RecipientPrefs":
        """收件人的語言、字級，以及他要不要收這兩類通知。

        三者一次取回：它們來自同一份 profile，分開查會讓每則推播多打一次
        資料庫，而這是逐筆推播的迴圈，成本會乘上待推播數。

        **開關看的是收件人自己的設定**，不是被通報對象的。每個人只決定自己
        收到什麼——用藥者不該替家屬決定要不要被通報，家屬也不該替用藥者關掉
        提醒。

        載入失敗時回傳預設值並視為兩者皆開啟：缺資料時沿用預設，與本專案其他
        降級方向一致。反過來（失敗即不送）會讓一次資料庫抖動變成整批服藥提醒
        靜默消失，那是本能力最不該發生的事。
        """
        if not self._user_profile_service or not user_id:
            return _RecipientPrefs(
                DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE, True, True
            )
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to load display prefs for user %s", user_id
            )
            return _RecipientPrefs(
                DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE, True, True
            )

        settings = (profile or {}).get("settings") or {}
        return _RecipientPrefs(
            normalize_user_language(settings.get("language")),
            normalize_user_font_size(settings.get("font_size")),
            # 欄位缺席時視為開啟——既有使用者的文件沒有這兩欄，不需要 backfill。
            bool(settings.get("notify_reminder", True)),
            bool(settings.get("notify_family", True)),
        )

    async def _dispatch(
        self,
        *,
        stage: str,
        log_id: str,
        claim: Callable[[str], Awaitable[bool]],
        release: Callable[[str], Awaitable[bool]],
        send: Callable[[], Awaitable[bool]],
    ) -> None:
        """
        推播權搶佔 → 推播 → 失敗還原。

        三個階段共用同一套流程，差別只在旗標與訊息內容。搶佔的理由見
        `MedicationLogRepository` 的「推播權搶佔」段落：查詢與標記之間沒有原子性，
        多實例並存時會重複推播。
        """
        try:
            claimed = await claim(log_id)
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to claim %s for log %s", stage, log_id
            )
            return

        if not claimed:
            # 旗標已被其他實例搶走，或先前的 tick 已送出。
            return

        try:
            sent = await send()
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to process %s for log %s", stage, log_id
            )
            sent = False

        if not sent:
            # 推播沒成功就把推播權還回去，下一個 tick 會重新搶佔並重試。
            with suppress(Exception):
                await release(log_id)

    async def _send_patient_reminder(
        self, log: MedicationLog, medication_cache: _TickMedicationNameCache
    ) -> bool:
        prefs = await self._resolve_prefs(log.user_id)
        if not prefs.notify_reminder:
            # 回 True 而非 False：`_dispatch` 的合約是「送失敗就把推播權還回去，
            # 下一個 tick 重新搶佔並重試」。關掉通知不是失敗，是這個階段已經
            # 處理完了——回 False 會讓它每 60 秒重試一次，永遠不會停。
            logger.info(
                "[MedicationScheduler] user %s opted out of reminders; skipping %s",
                log.user_id,
                log.id,
            )
            return True
        language, font_size = prefs.language, prefs.font_size
        # 用藥者的提醒卡要看得出「哪一顆」，走 get_entries() 帶出縮圖 URL；
        # 家屬警報只需要藥名，見 _send_caregiver_alert 仍是 get()。
        medication_entries = await medication_cache.get_entries(log)
        flex_msg = build_patient_medication_flex(
            log_id=log.id,
            slot_type=log.slot_type,
            scheduled_time=to_taipei_hm(log.scheduled_at, default="08:00"),
            disabled=False,
            medication_names=medication_entries,
            language=language,
            font_size=font_size,
        )
        return await self._replier.push_flex(log.user_id, flex_msg)

    async def _send_urgent_reminder(
        self, log: MedicationLog, medication_cache: _TickMedicationNameCache
    ) -> bool:
        prefs = await self._resolve_prefs(log.user_id)
        if not prefs.notify_reminder:
            # T+20 催促與 T+0 提醒是同一件事的兩次，受同一個開關管。
            return True
        language, font_size = prefs.language, prefs.font_size
        medication_entries = await medication_cache.get_entries(log)
        urgent_flex = build_patient_urgent_reminder_flex(
            log_id=log.id,
            slot_type=log.slot_type,
            scheduled_time=to_taipei_hm(log.scheduled_at, default="08:00"),
            medication_names=medication_entries,
            language=language,
            font_size=font_size,
        )
        return await self._replier.push_flex(log.user_id, urgent_flex)

    async def _resolve_patient_name(self, user_id: str) -> str:
        """取得用藥者的顯示名稱；查不到時回退為泛稱。"""
        if not self._user_profile_service:
            return "成員"
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
            if profile and isinstance(profile, dict) and profile.get("name"):
                return profile["name"]
        except Exception:
            pass
        return "成員"

    async def _send_caregiver_alert(
        self, log: MedicationLog, medication_cache: _TickMedicationNameCache
    ) -> bool:
        patient_name = await self._resolve_patient_name(log.user_id)
        # 藥名查表與 T+0／T+20 共用同一套機制（見 _TickMedicationNameCache）：
        # 家屬警報同樣是一個 tick 內可能有多筆，逐筆查「規則→藥品」沒有道理。
        medication_names = await medication_cache.get(log)

        # 這則推播的收件人是家屬，語言、字級與通知意願都取家屬本人的設定。
        # 用藥者關掉自己的提醒不影響家屬收不收得到逾時通報，反之亦然。
        prefs = await self._resolve_prefs(log.alert_notify_user_id)
        if not prefs.notify_family:
            # 抑制的只有推播。`claim_caregiver_alert` 在搶佔的同一次更新裡就
            # 已把 status 設為 missed，因此紀錄仍然正確——家屬事後在 LIFF 上
            # 看得到這個時段錯過了，只是當下不會被推播打擾。
            logger.info(
                "[MedicationScheduler] caregiver %s opted out of family alerts; "
                "skipping %s",
                log.alert_notify_user_id,
                log.id,
            )
            return True
        language, font_size = prefs.language, prefs.font_size
        alert_flex = build_caregiver_alert_flex(
            patient_name=patient_name,
            slot_type=log.slot_type,
            scheduled_time=to_taipei_hm(log.scheduled_at, default="08:00"),
            medication_names=medication_names,
            language=language,
            font_size=font_size,
        )
        return await self._replier.push_flex(log.alert_notify_user_id, alert_flex)

    async def _resolve_suppressed_reminder_ids(
        self, reminders: list["MedicationReminder"], date_str: str
    ) -> set[str]:
        """挑出「有掛藥，但當日一顆有效的都不剩」的規則 id。

        規則與藥品各自帶日期區間，卻是由不同的寫入路徑決定的：規則的
        `end_date` 在 `find_or_create_reminder` 的 `$setOnInsert` 一律是 None
        （長期有效），藥品的 `end_date` 則由處方箋的療程天數換算。療程結束後
        兩邊就脫鉤——規則照常每天展開，藥品清單卻已經全數失效，推出去的是一張
        說不出要吃什麼的空卡片。對高齡使用者而言那比不推更糟。

        修正的方式是讓「這個時段今天要不要推」改由「今天還有沒有有效的藥」
        推導，而不是由規則自己的日期區間獨立判斷。刻意不改成把療程結束日回寫
        到規則上：規則是 `(user_id, slot_type)` 唯一的共用容器，同一個 08:00
        底下會同時掛著這張處方的五天療程、另一張處方的十四天療程、以及慢性病
        長期用藥（`end_date` 為 None），單一個欄位表達不了這件事；而且
        `find_or_create_reminder` 在復活規則時本來就會把過期的 `end_date` 清空，
        回寫的值撐不過下一次同時段的處方提交。

        `medication_ids` 為空的規則不在抑制範圍內：本功能導入前建立的規則都是
        空陣列，它們本來就沒有藥品清單可言，版面與行為必須與過去一致（見
        `medication_flex._medication_list_block`）。要抑制的只有「掛了藥、但藥
        全部失效」這一種，不是「沒掛藥」。

        查詢失敗時回傳空集合，也就是不抑制任何規則：一次藥品查詢失敗不該讓
        整批使用者當天收不到提醒。少推一張空卡片與漏推一次真正該吃的藥相比，
        後者的代價高得多。
        """
        linked = {
            reminder.id: reminder.medication_ids
            for reminder in reminders
            if reminder.id and reminder.medication_ids
        }
        if not linked:
            return set()

        medication_ids = sorted({mid for ids in linked.values() for mid in ids})
        try:
            medications = await self._medication_repository.find_active_by_ids(
                medication_ids, date_str
            )
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to resolve active medications for "
                "reminder suppression; proceeding without suppressing any reminder"
            )
            return set()

        active_ids = {medication.id for medication in medications}
        return {
            reminder_id
            for reminder_id, ids in linked.items()
            if not any(mid in active_ids for mid in ids)
        }

    async def _notify_missed_summary(
        self, misfired_by_caregiver: dict[str, list[MedicationLog]]
    ) -> None:
        """
        把本次 tick 新發現的錯過時段，依家屬彙整成一則通知送出。

        不做推播權搶佔：來源是 `upsert_log` 回報的 created 旗標，而 (reminder_id,
        scheduled_at) 唯一索引保證同一個時段只會被插入一次，所以多實例並存時也只有
        真正插入成功的那個實例會拿到這些 log。

        送不出去就只記 log，不重試：這是中斷後的補充告知，為它額外維護一份「待通知」
        狀態並不划算，而錯過的時段本身已經以 status=missed 留在資料庫裡。
        """
        name_cache: dict[str, str] = {}

        for caregiver_id, logs in misfired_by_caregiver.items():
            if not caregiver_id:
                continue
            # 錯過時段的彙整通知與 T+30 逾時通報是同一類訊息（都是「你關心的
            # 人漏服了」），受同一個 notify_family 開關管。錯過的時段本身仍以
            # status=missed 留在資料庫，家屬事後查得到。
            prefs = await self._resolve_prefs(caregiver_id)
            if not prefs.notify_family:
                continue
            try:
                entries: list[dict[str, str]] = []
                for log in sorted(logs, key=lambda item: ensure_aware_utc(item.scheduled_at)):
                    if log.user_id not in name_cache:
                        name_cache[log.user_id] = await self._resolve_patient_name(
                            log.user_id
                        )
                    entries.append(
                        {
                            "patient_name": name_cache[log.user_id],
                            "slot_type": log.slot_type,
                            "scheduled_time": to_taipei_hm(
                                log.scheduled_at, default="08:00"
                            ),
                        }
                    )

                language, font_size = await self._resolve_display_prefs(caregiver_id)
                await self._replier.push_flex(
                    caregiver_id,
                    build_caregiver_missed_summary_flex(
                        missed=entries, language=language, font_size=font_size
                    ),
                )
            except Exception:
                logger.exception(
                    "[MedicationScheduler] Failed to send missed-slot summary to %s",
                    caregiver_id,
                )

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        # 登記心跳：排程器與 API 拆成不同 pod 之後，這是 K8s 唯一能判斷
        # 「排程器還在跑」的依據——uvicorn 活著不代表這個 task 還在。
        scheduler_heartbeat.register(
            HEARTBEAT_NAME, expected_interval_seconds=self._check_interval_seconds
        )
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[MedicationScheduler] Background scheduler started")

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        logger.info("[MedicationScheduler] Background scheduler stopped")

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.process_ticks()
            except Exception:
                logger.exception("[MedicationScheduler] Error during tick execution")
            # 心跳放在 except 之外：單次 tick 失敗（例如資料庫瞬斷）不代表排程器
            # 停擺，迴圈本身仍在轉，重啟這個 pod 只會讓情況更糟——重啟期間錯過
            # 的時段不會補推。心跳要回答的是「這個迴圈還在不在」，不是「這一輪
            # 有沒有成功」。
            scheduler_heartbeat.beat(HEARTBEAT_NAME)
            await asyncio.sleep(self._check_interval_seconds)

    async def process_ticks(self, now: Optional[datetime] = None) -> None:
        """執行一次排程檢查 (可代入特定的 now 時間供單元測試或實機測試)"""
        current_time = now or datetime.now(TAIPEI_TZ)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=TAIPEI_TZ)

        today_date_str = current_time.strftime("%Y-%m-%d")
        current_hm_str = current_time.strftime("%H:%M")

        # 錯過超過 grace 的時段不再推播（見 DEFAULT_MISFIRE_GRACE_MINUTES）。
        misfire_cutoff = current_time - timedelta(minutes=self._misfire_grace_minutes)

        # ── 階段 1：T+0min 首刷提醒建立與推播 ──────────────────────────
        # 1. 查詢今日所有已到期 (scheduled_time <= current_hm_str) 的活躍提醒並為其 upsert 當日 log
        active_reminders = await self._reminder_repository.list_active_reminders_up_to_time(
            max_scheduled_time=current_hm_str, target_date_str=today_date_str
        )
        # 時段還在、但底下已經沒有任何當日有效的藥：這一輪不為它展開紀錄，
        # 也把先前已經展開、還沒確認的紀錄作廢（見 _resolve_suppressed_reminder_ids）。
        suppressed_reminder_ids = await self._resolve_suppressed_reminder_ids(
            active_reminders, today_date_str
        )
        if suppressed_reminder_ids:
            # 作廢範圍只到今天：規則「今天沒有有效藥品」是今天的判斷，回頭動到
            # 更早的紀錄會改寫當時確實有藥的事實。日界的算法與下方 scheduled_dt
            # 一致，兩者必須用同一個時區基準，否則作廢範圍會與展開範圍對不上。
            today_start = datetime.strptime(today_date_str, "%Y-%m-%d").replace(
                tzinfo=current_time.tzinfo
            )
            try:
                cancelled = await self._log_repository.cancel_pending_by_reminder_ids(
                    sorted(suppressed_reminder_ids), scheduled_from=today_start
                )
            except Exception:
                logger.exception(
                    "[MedicationScheduler] Failed to cancel pending logs for reminders "
                    "with no active medication"
                )
            else:
                # 只在真的改動了紀錄時才記錄。抑制判定每 60 秒重算一次，無條件
                # 記錄會讓同一批規則每分鐘重印一行——這正是下方 misfire 訊息
                # 要避免的問題。有狀態轉換才值得留一行。
                if cancelled:
                    logger.info(
                        "[MedicationScheduler] Cancelled %d pending log(s) across %d "
                        "reminder(s) with no active medication on %s",
                        cancelled,
                        len(suppressed_reminder_ids),
                        today_date_str,
                    )

        # 本次 tick 才發現的錯過時段，依通報家屬分組，稍後彙整成一則通知。
        misfired_by_caregiver: dict[str, list[MedicationLog]] = {}
        for reminder in active_reminders:
            try:
                # 沒有有效藥品的時段不展開紀錄——沒有紀錄，後續三個階段的
                # list_pending_* 就挑不到它，T+0／T+20／T+30 三則推播自然全部
                # 停下，不需要在推播路徑上多做一次規則的 join。
                if reminder.id in suppressed_reminder_ids:
                    continue

                scheduled_dt = datetime.strptime(
                    f"{today_date_str} {reminder.scheduled_time}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=current_time.tzinfo)
                timeout_dt = scheduled_dt + timedelta(minutes=30)

                # 不為「提醒建立之前」的時段補建 log。
                # 否則 20:00 新增一筆早上 08:00 的提醒，會在同一個 tick 內連續
                # 觸發首刷提醒、T+20 催促、以及 T+30 家屬逾時警報（全是假的）。
                if scheduled_dt < ensure_aware_utc(reminder.created_at):
                    continue

                # 停機期間錯過的時段：仍建立 log 留下紀錄，但直接記為 missed 且三個
                # 旗標全部設起，不推播。上面的 created_at 檢查只擋得住「提醒是後來才
                # 建立的」，擋不住「服務當時沒在跑」——下午三點重啟時，早上 08:00 與
                # 中午 12:00 的 log 會在同一個 tick 內被建立，接著三個階段依序判定成立，
                # 使用者一次收到四則、家屬收到兩則逾時警報。
                is_misfired = scheduled_dt < misfire_cutoff

                log_data = MedicationLog(
                    reminder_id=reminder.id,
                    user_id=reminder.user_id,
                    alert_notify_user_id=reminder.creator_user_id,
                    slot_type=reminder.slot_type,
                    scheduled_at=scheduled_dt,
                    timeout_at=timeout_dt,
                    status="missed" if is_misfired else "pending",
                    patient_reminder_sent=is_misfired,
                    urgent_reminder_sent=is_misfired,
                    caregiver_alert_sent=is_misfired,
                )
                # upsert 是 $setOnInsert，所以只有「第一次建立」會套用上面的靜默標記；
                # 正常運行中早就建好的 log 不會被這裡蓋掉。
                saved_log, created = await self._log_repository.upsert_log(log_data)

                # 只有「本次才建立」的錯過時段要通知；否則每 60 秒的 tick 都會重算出
                # 同一批 is_misfired，家屬會被同一則通知洗版。
                #
                # 這行 log 也受同一個 created 把關，而且必須放在 upsert 之後：
                # is_misfired 在每一輪都會對同一個時段重新算成 True，先前把它印在
                # upsert 之前又不看 created，等於同一個時段每 60 秒重印一次
                # ——一位使用者一天約四千行。措辭也對齊實情：這裡記錄的是「本次
                # 才建立、且判定為錯過」的時段，不是每次重新判定的結果。
                if created and is_misfired:
                    logger.info(
                        "[MedicationScheduler] Misfired slot recorded without push: "
                        "reminder=%s scheduled=%s grace=%dmin",
                        reminder.id,
                        scheduled_dt.isoformat(),
                        self._misfire_grace_minutes,
                    )
                    misfired_by_caregiver.setdefault(
                        reminder.creator_user_id, []
                    ).append(saved_log)
            except Exception:
                logger.exception(
                    f"[MedicationScheduler] Failed to upsert T+0min log for user {reminder.user_id}"
                )

        # 1b. 中斷期間錯過的時段：每位家屬彙整成一則通知（措辭與 T+30 逾時警報不同）
        if misfired_by_caregiver:
            await self._notify_missed_summary(misfired_by_caregiver)

        # 2. 使用 list_pending_patient_reminders 查詢已到期 (scheduled_at <= current_time) 且未發送首刷的紀錄推播
        pending_initial_logs = await self._log_repository.list_pending_patient_reminders(
            threshold_time=current_time
        )
        # 這批 log 共用同一份藥名查表（見 _TickMedicationNameCache）：許多使用者共用
        # 同一個時段（例如 08:00）時，藥名查詢不會隨 log 數量線性增加。這裡只是建立
        # 查表物件本身（不發查詢），迴圈與 _dispatch 的搶佔／推播流程完全不變。
        initial_medication_cache = self._medication_cache(pending_initial_logs)
        for log in pending_initial_logs:
            await self._dispatch(
                stage="T+0min initial reminder",
                log_id=log.id,
                claim=self._log_repository.claim_patient_reminder,
                release=self._log_repository.release_patient_reminder,
                send=partial(self._send_patient_reminder, log, initial_medication_cache),
            )

        # ── 階段 2：T+20min 第二次溫馨催促 ─────────────────────────────
        # 門檻計算：找出 scheduled_at <= (當前時間 - 20分鐘) 的記錄
        # 說明：採用 $lte (小於等於) 能有效容忍執行秒數偏差或伺服器重啟延遲，絕不漏發。
        #       配合 urgent_reminder_sent 標記與單一 Document 狀態變更，保證不會重複發送。
        urgent_threshold = current_time - timedelta(minutes=20)
        pending_urgent_logs = await self._log_repository.list_pending_urgent_reminders(
            threshold_time=urgent_threshold
        )
        urgent_medication_cache = self._medication_cache(pending_urgent_logs)
        for log in pending_urgent_logs:
            await self._dispatch(
                stage="T+20min urgent reminder",
                log_id=log.id,
                claim=self._log_repository.claim_patient_urgent_reminder,
                release=self._log_repository.release_patient_urgent_reminder,
                send=partial(self._send_urgent_reminder, log, urgent_medication_cache),
            )

        # ── 階段 3：T+30min 第三次家屬逾時警報 ─────────────────────────
        # 門檻計算：找出 timeout_at <= 當前時間 且狀態仍為 pending (未確認用藥) 的記錄
        pending_alert_logs = await self._log_repository.list_pending_caregiver_alerts(
            threshold_time=current_time
        )
        alert_medication_cache = self._medication_cache(pending_alert_logs)
        for log in pending_alert_logs:
            await self._dispatch(
                stage="T+30min caregiver alert",
                log_id=log.id,
                claim=self._log_repository.claim_caregiver_alert,
                release=self._log_repository.release_caregiver_alert,
                send=partial(self._send_caregiver_alert, log, alert_medication_cache),
            )



def start_medication_scheduler(
    *,
    enabled: bool = True,
    replier: LineReplier,
    user_profile_service: Optional[UserProfileService] = None,
) -> Optional[MedicationScheduler]:
    if not enabled:
        logger.info("[MedicationScheduler] disabled")
        return None

    scheduler = MedicationScheduler(
        replier=replier,
        user_profile_service=user_profile_service,
    )
    scheduler.start()
    return scheduler
