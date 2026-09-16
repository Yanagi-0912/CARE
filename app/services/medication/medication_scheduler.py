import logging
import random
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Callable, Optional

from app.core.request_logging import log_stage
from app.models.medication import (
    CAREGIVER_ALERT_AFTER_ANCHOR_MINUTES,
    DEFAULT_MISFIRE_GRACE_MINUTES,
    TAIPEI_TZ,
    URGENT_AFTER_ANCHOR_MINUTES,
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
    MedicationGroup,
    MedicationListEntry,
    build_caregiver_alert_flex,
    build_caregiver_missed_summary_flex,
    build_patient_medication_flex,
    build_patient_urgent_reminder_flex,
)
from app.services.line_messaging.reply.reply import LineReplier
from app.services.line_messaging.send_result import SendOutcome, SendResult
from app.services.medication.drug_appearance_image_service import (
    resolve_drug_appearance_image_url,
)
from app.services.medication.reminder_variants import (
    TONE_CONTROL,
    VariantStats,
    build_variant_stats,
    choose_variant,
)
from app.services.scheduling.push_tick_scheduler import (
    DispatchOutcome,
    PushTickScheduler,
    RecipientPrefs,
    SendReturn,
)
from app.services.users.user_profile_service import UserProfileService

logger = logging.getLogger(__name__)

# 心跳登記名稱。用藥提醒是每 60 秒一輪的短週期排程，因此它的心跳是判斷
# 「排程器 pod 是否還健康」最靈敏的訊號——每日諮詢摘要睡到隔天才醒，
# 停擺一整天都還在它的正常範圍內，當不了 liveness 依據。
HEARTBEAT_NAME = "medication"

# T+0 在排定時刻之後多久以內送出算「準時」。每一輪的間隔是「處理時間＋60 秒」，
# 排定時刻之後的第一輪可能晚超過 60 秒才開始，所以取兩輪。（推播失敗後的重送不在
# 考量內：選項在第一次嘗試就寫進紀錄，重送時沿用、不重新判斷。）超過這個時間才送
# 的那一頓（排程器停過、長輩把時段改到剛過去、時段剛重新啟用），拉霸只挑語氣、不挑
# 催促時機：催促若照 +10 算，可能在送 T+0 的同一輪就緊接著送出（見
# DEFAULT_MISFIRE_GRACE_MINUTES）。
_ON_TIME_T0 = timedelta(minutes=2)

# 逾時通報的推播種類。家屬名單走家庭授權的通知政策（`NOTIFICATION_POLICY`），
# 與高風險藥物、緊急通報、掛號提醒是同一個 resolver，不在這裡另外判斷誰是家屬。
NOTIFICATION_KIND = "medication_missed"


# 收件人偏好的型別已移到與掛號提醒共用的排程骨架（push_tick_scheduler）；
# 這個名稱保留給既有的 import。
_RecipientPrefs = RecipientPrefs


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

    `get_groups()` 是同一批查詢結果的第三種讀法：依規則的 `entries` 分區
    （飯前／飯後／無關聯），供 T+0／T+20 用藥者卡片依時機分組呈現並排除
    已確認的藥（design 決策 6）。三支讀法共用同一次 `_load()`，呼叫順序或
    次數都不會讓查詢次數增加。
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
        # `get_groups` 額外需要的兩份索引，由 `_load` 在同一批查詢裡順便建好：
        # 規則本身（要讀 `entries` 做分區）與「每個台北日期各自的藥品查表」
        # （與 `entries_by_log_id` 用的是同一份 `entry_by_id`，只是多留一份
        # 以日期為 key 的版本，不必重新發查詢或重算縮圖）。兩者預設為空字典
        # 而非 None：`_load` 提早結束的分支（沒有 reminder_ids／查詢失敗）
        # 不會補設它們，`get_groups` 因此自然退化成空清單，不需要另外判斷
        # 「有沒有載入過」。
        self._reminder_by_id: dict[str, "MedicationReminder"] = {}
        self._entry_by_id_by_date: dict[str, dict[str, MedicationListEntry]] = {}

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
        """取得指定 log 的扁平藥品清單列（藥名＋縮圖 URL）；供用藥者服藥提醒／
        二次催促卡片的 `medication_names` 使用，以及其他需要縮圖的呼叫端。

        第一次呼叫（不論是這支、`get` 還是 `get_groups`）才會真的發出查詢，
        之後都讀已經查好的結果，理由見 class docstring。
        """
        await self._ensure_loaded(
            reminder_collection=reminder_collection,
            medication_collection=medication_collection,
        )
        return self._entries_by_log_id.get(log.id, [])

    async def get_groups(
        self,
        log: "MedicationLog",
        *,
        reminder_collection: Optional[Any] = None,
        medication_collection: Optional[Any] = None,
    ) -> list["MedicationGroup"]:
        """取得指定 log 依服藥時機分區的資料；供 T+0／T+20 用藥者卡片使用
        （spec「三階遞進推播」、design 決策 6）。

        分組語意與 `MedicationService.medication_groups_for_log`（單筆、
        dispatcher 路徑）完全一致：依 `reminder.entries` 的順序（已由模型
        驗證器排過 `MEAL_TIMING_ORDER`），每個條目的 `items` 是該條目裡、在
        log 台北日期當日有效、且尚未出現在 `taken_medication_ids` 的藥；
        `items` 為空的條目直接跳過。差別只在於這裡讀的是 `_load` 已經批次
        查好的 `_reminder_by_id`／`_entry_by_id_by_date`，不會為了分組另外
        發查詢——即使先呼叫過 `get_entries` 也一樣，兩者共用同一次 `_load`。

        規則不存在（查詢失敗或已被刪除）時回傳空清單，卡片退回沒有分區
        的原樣，與 `get_entries` 同一個降級方向。
        """
        await self._ensure_loaded(
            reminder_collection=reminder_collection,
            medication_collection=medication_collection,
        )
        reminder = self._reminder_by_id.get(log.reminder_id)
        if not reminder:
            return []

        date_str = ensure_aware_utc(log.scheduled_at).astimezone(TAIPEI_TZ).strftime("%Y-%m-%d")
        entry_by_id = self._entry_by_id_by_date.get(date_str, {})
        taken_ids = set(log.taken_medication_ids)

        groups: list[MedicationGroup] = []
        for entry in reminder.entries:
            items = [
                (medication_id, entry_by_id[medication_id])
                for medication_id in entry.medication_ids
                if medication_id in entry_by_id and medication_id not in taken_ids
            ]
            if items:
                groups.append(
                    MedicationGroup(
                        meal_timing=entry.meal_timing,
                        scheduled_time=entry.scheduled_time,
                        items=items,
                    )
                )
        return groups

    async def _ensure_loaded(
        self,
        *,
        reminder_collection: Optional[Any],
        medication_collection: Optional[Any],
    ) -> None:
        """第一次呼叫才真的發查詢，之後都是 no-op——理由見 class docstring。

        `get_entries`／`get_groups` 都經這支，確保同一個 tick 內不論呼叫
        順序或次數，`_load` 只跑一次。
        """
        if self._entries_by_log_id is None:
            self._entries_by_log_id = await self._load(
                reminder_collection=reminder_collection,
                medication_collection=medication_collection,
            )

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
        # `get_groups` 讀 `entries`（分區用），這批 reminder 已經是同一次查詢
        # 撈回來的完整物件，直接留一份參照，不必為分組另外查一次。
        self._reminder_by_id = reminder_by_id

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
            # `get_groups` 依日期分組查表：同一份 entry_by_id，多留一份以
            # 日期為 key 的版本，供分區時查該條目的藥是否當日有效、要不要
            # 顯示縮圖，不重算、不再查一次。
            self._entry_by_id_by_date[date_str] = entry_by_id
            for log in logs_on_date:
                reminder = reminder_by_id.get(log.reminder_id)
                if not reminder:
                    entries_by_log_id[log.id] = []
                    continue
                entries_by_log_id[log.id] = [
                    entry_by_id[mid] for mid in reminder.medication_ids if mid in entry_by_id
                ]
        return entries_by_log_id


def _worst_case_for_retry(failures: list[SendResult]) -> SendResult:
    """多位收件人全部失敗時，挑一個代表整則的結果。

    暫時性失敗優先（重試才有機會送到任何一位），其次是額度用完（不計次還回
    去，額度恢復就送），全部都是明確拒絕才放棄——一位家屬封鎖了官方帳號、
    另一位只是網路瞬斷，這一則不該因為前者而放棄後者。
    """
    for outcome in (SendOutcome.TRANSIENT, SendOutcome.UNAUTHORIZED, SendOutcome.QUOTA_EXCEEDED):
        for failure in failures:
            if failure.outcome is outcome:
                return failure
    return failures[0]


class MedicationScheduler(PushTickScheduler):
    """
    雙階遞進定時排程引擎 (MedicationScheduler)
    1. T+0min  首刷提醒建立與推播
    2. T+20min 第二次溫馨催促推播 (若逾時未用藥)；實際在最晚服藥時刻後 10／15／20
       分鐘，由用藥提醒拉霸替每一頓挑選（見 reminder_variants.py）
    3. T+30min 第三次家屬逾時通報警報 (若仍未用藥)

    迴圈、心跳、推播權搶佔與收件人偏好解析在 `PushTickScheduler`，與掛號提醒
    （`AppointmentScheduler`）共用；這裡只剩用藥特有的展開判定與三階文案。
    """

    HEARTBEAT_NAME = HEARTBEAT_NAME
    LOG_PREFIX = "[MedicationScheduler]"

    def __init__(
        self,
        replier: LineReplier,
        user_profile_service: Optional[UserProfileService] = None,
        check_interval_seconds: int = 60,
        misfire_grace_minutes: int = DEFAULT_MISFIRE_GRACE_MINUTES,
        reminder_repository=MedicationReminderRepository,
        log_repository=MedicationLogRepository,
        medication_repository=MedicationRepository,
        variant_sample: Optional[Callable[[float, float], float]] = None,
        authorization_service: Any = None,
    ) -> None:
        super().__init__(
            replier=replier,
            user_profile_service=user_profile_service,
            check_interval_seconds=check_interval_seconds,
        )
        self._misfire_grace_minutes = misfire_grace_minutes
        # 家庭授權服務：T+30 逾時通報與停機彙整的家屬名單都由它決定（見
        # `_family_recipients`）。正式路徑一律注入；只有測試會不帶。
        self._authorization_service = authorization_service
        # 三個 repository 全部走注入，預設就是真正的那三個 class（方法皆為
        # staticmethod，傳 class 本身即可當成物件用），因此正式路徑的行為與
        # 注入前完全相同。開這個縫是為了讓測試餵假的 repository，不必用
        # monkeypatch 換掉本模組 import 進來的名稱——openspec 的測試規則明文
        # 禁止後者，慣例與 MedicationService 一致。
        self._reminder_repository = reminder_repository
        self._log_repository = log_repository
        self._medication_repository = medication_repository
        # 用藥提醒拉霸的抽樣函式（從 Beta 分佈抽一個值）。測試注入期望值讓挑選
        # 可預期；正式路徑用 SystemRandom——不需要可重現，而 SonarCloud 會把
        # random 模組的偽亂數標成安全熱點，此專案又不理會 NOSONAR。
        self._variant_sample = variant_sample or random.SystemRandom().betavariate
        # 一個 tick 內收件人偏好的查表，`process_ticks` 開頭清空。展開階段現在
        # 要看每位用藥者的 notify_reminder（見 process_ticks 的「不追蹤」判定），
        # 那是對「今天所有到期規則」逐筆的判斷；沒有這份快取，同一位使用者
        # 四個時段就查四次 profile，再加上三個推播階段各查一次。
        self._prefs_cache: dict[str, RecipientPrefs] = {}

    async def _resolve_prefs(self, user_id: str) -> RecipientPrefs:
        """同 `PushTickScheduler._resolve_prefs`，但一個 tick 內同一個人只查一次。"""
        cached = self._prefs_cache.get(user_id)
        if cached is not None:
            return cached
        prefs = await super()._resolve_prefs(user_id)
        self._prefs_cache[user_id] = prefs
        return prefs

    async def _is_untracked(self, user_id: str) -> bool:
        """這位用藥者關掉了「用藥提醒通知」（notify_reminder=False）。

        語意定為「這個人的服藥不追蹤」，不只是「不推給他」：以前只擋 T+0／T+20
        的推播，紀錄照樣展開、30 分鐘後照樣被判成漏服、家屬照樣收到「他漏吃了」
        ——他關掉的明明是提醒，結果變成家人每天被通知他沒吃藥，而他從頭到尾
        沒收到任何提醒可以回應。所以展開階段直接不為他建立紀錄；三個推播階段
        再各擋一次，是給本判定落地前、或他在當天中途關掉之後已經展開的紀錄用
        的（那些紀錄會被註銷，不會變成漏服）。
        """
        return not (await self._resolve_prefs(user_id)).notify_reminder

    async def _cancel_untracked_log(self, log: MedicationLog, stage: str) -> None:
        """把不追蹤的用藥者當天已展開的紀錄註銷，讓三個階段都挑不到它。

        註銷而不是留在 pending：留著的話 T+30 每個 tick 都會再查到它，而且它會
        在服藥狀況查詢裡以「尚未確認」出現——對一位選擇不被追蹤的人，那是他
        沒有做過的事。`cancelled` 不進用藥歷史，正好是這個語意。
        """
        try:
            cancelled = await self._log_repository.cancel_pending_by_reminder(
                log.reminder_id
            )
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to cancel untracked log %s at %s",
                log.id,
                stage,
            )
            return
        logger.info(
            "[MedicationScheduler] user %s opted out of reminders; cancelled %d pending "
            "log(s) of reminder %s instead of running %s",
            log.user_id,
            cancelled,
            log.reminder_id,
            stage,
        )

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

    async def _send_patient_reminder(
        self,
        log: MedicationLog,
        medication_cache: _TickMedicationNameCache,
        variant_stats: Optional[VariantStats] = None,
        now: Optional[datetime] = None,
    ) -> SendReturn:
        prefs = await self._resolve_prefs(log.user_id)
        if not prefs.notify_reminder:
            # SKIPPED 而非 RETRY：關掉通知不是失敗，是這個階段已經處理完了
            # ——當成失敗會讓它每 60 秒重試一次，永遠不會停。寫略過標記而不是
            # 送達標記：T+20／T+30 只對真的送達的那一頓進行。（正常情況下不追蹤
            # 的用藥者根本不會有紀錄，見 process_ticks；這裡是後盾。）
            logger.info(
                "[MedicationScheduler] user %s opted out of reminders; skipping %s",
                log.user_id,
                log.id,
            )
            return DispatchOutcome.SKIPPED
        language, font_size = prefs.language, prefs.font_size
        # 關掉提醒的那一頓在上面就回傳了，不會進拉霸：訊息沒送出去，沒有東西可學。
        tone = await self._reminder_tone(log, variant_stats, now)
        # 用藥者的提醒卡要看得出「哪一顆」，走 get_entries() 帶出縮圖 URL；
        # 家屬警報只需要藥名，見 _send_caregiver_alert 仍是 get()。
        medication_entries = await medication_cache.get_entries(log)
        # `medication_groups` 供分區版面用（Task 6）；`medication_names` 仍照舊
        # 傳完整列，現有版面不變，見 build_patient_medication_flex 的說明。
        flex_msg = build_patient_medication_flex(
            log_id=log.id,
            slot_type=log.slot_type,
            scheduled_time=to_taipei_hm(log.scheduled_at, default="08:00"),
            disabled=False,
            medication_names=medication_entries,
            medication_groups=await medication_cache.get_groups(log),
            language=language,
            font_size=font_size,
            tone=tone,
        )
        return await self._push_result(log.user_id, flex_msg)

    async def _reminder_tone(
        self,
        log: MedicationLog,
        stats: Optional[VariantStats],
        now: Optional[datetime] = None,
    ) -> str:
        """這一頓 T+0 用哪種語氣，並把拉霸的選擇寫進紀錄。

        推播失敗後重送時，紀錄上已經有選項，照用、不重挑。`stats` 為 None 代表
        這一輪讀不到歷史（或呼叫端沒給）。拉霸出任何狀況都退回現行版本照常送出
        ——提醒不能因為學習機制出錯而送不出去；退回時不寫入選項，這一頓也就不會
        被當成拉霸的結果拿去學。

        準時送出的 T+0 才套用拉霸挑的催促時機（改寫 urgent_at）；晚送的只挑語氣，
        催促維持展開時的預設值，理由見 `_ON_TIME_T0`。
        """
        if log.reminder_tone:
            return log.reminder_tone
        if stats is None:
            return TONE_CONTROL
        sent_at = ensure_aware_utc(now or datetime.now(TAIPEI_TZ))
        on_time = sent_at - ensure_aware_utc(log.scheduled_at) <= _ON_TIME_T0
        try:
            variant = choose_variant(
                log, stats, sample=self._variant_sample, apply_timing=on_time
            )
            urgent_at = None
            if variant.nudge_minutes is not None:
                # 催促從最晚服藥時刻起算（飯前飯後分兩批時，最晚那批才是該催的
                # 時刻），不是從 T+0 的 scheduled_at。
                anchor = ensure_aware_utc(log.timeout_at) - timedelta(
                    minutes=CAREGIVER_ALERT_AFTER_ANCHOR_MINUTES
                )
                urgent_at = anchor + timedelta(minutes=variant.nudge_minutes)
            stored = await self._log_repository.assign_reminder_variant(
                log.id,
                tone=variant.tone,
                nudge_minutes=variant.nudge_minutes,
                urgent_at=urgent_at,
                # 挑選時看到的逾時時間。長輩剛好改了提醒設定時寫入會落空，不會
                # 用舊的最晚時刻蓋掉剛對齊好的催促時間。
                expected_timeout_at=log.timeout_at,
            )
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to record reminder variant for %s; "
                "re-reading the log to stay consistent",
                log.id,
            )
            stored = await self._reread_log(log.id)
        if stored is None or not stored.reminder_tone:
            return TONE_CONTROL
        log_stage(logger, "med_variant", tone=stored.reminder_tone, nudge=stored.nudge_minutes)
        return stored.reminder_tone

    async def _reread_log(self, log_id: str) -> Optional[MedicationLog]:
        """寫入時出錯，但寫入可能其實已經生效（例如回應在網路上丟了）：重讀一次，
        讓 T+0 卡片跟資料庫記的那一版一致（T+20 會照資料庫送）。讀不到就當作沒寫入。"""
        try:
            return await self._log_repository.get_log_by_id(log_id)
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to re-read log %s; sending current wording",
                log_id,
            )
            return None

    async def _load_variant_stats(self) -> Optional[VariantStats]:
        """讀出拉霸學習用的歷史並彙整成計數表；失敗時回 None，這一輪的 T+0 全部照
        現行版本送。"""
        try:
            return build_variant_stats(await self._log_repository.list_variant_outcomes())
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to load reminder variant history; "
                "this tick sends current wording"
            )
            return None

    async def _send_urgent_reminder(
        self, log: MedicationLog, medication_cache: _TickMedicationNameCache
    ) -> SendReturn:
        prefs = await self._resolve_prefs(log.user_id)
        if not prefs.notify_reminder:
            # T+20 催促與 T+0 提醒是同一件事的兩次，受同一個開關管。
            return DispatchOutcome.SKIPPED
        language, font_size = prefs.language, prefs.font_size
        medication_entries = await medication_cache.get_entries(log)
        urgent_flex = build_patient_urgent_reminder_flex(
            log_id=log.id,
            slot_type=log.slot_type,
            scheduled_time=to_taipei_hm(log.scheduled_at, default="08:00"),
            medication_names=medication_entries,
            medication_groups=await medication_cache.get_groups(log),
            language=language,
            font_size=font_size,
            # 與同一頓的 T+0 同一種語氣；拉霸上線前的紀錄沒有這個欄位，照現行版本。
            tone=log.reminder_tone or TONE_CONTROL,
        )
        return await self._push_result(log.user_id, urgent_flex)

    async def _send_caregiver_alert(
        self, log: MedicationLog, medication_cache: _TickMedicationNameCache
    ) -> SendReturn:
        """T+30 家屬逾時通報，逐一推給家庭授權選出的每位家屬。

        以前只送給規則的建立者（`alert_notify_user_id`）：家屬替長輩設的提醒家屬
        收得到，長輩自己在 LIFF 設的則推回長輩本人，家屬一則都收不到。

        回傳值與掛號提醒的 `_fan_out` 同一個判定：至少送達一人算送達、沒有任何
        人該收算略過；只有「該收的人全部送失敗」或「名單判定失敗」才把推播權
        還回去重試（有上限，見 `release_caregiver_alert`）。部分失敗不重試——
        重試會讓已經收到的人再收一次，一位封鎖官方帳號的家屬就能讓其他人被
        連環轟炸。全部失敗時取「最值得重試」的那種失敗當結果：有一位是暫時性
        失敗就重試（額度用完也一樣要等額度恢復），全部都是明確拒絕才放棄。
        """
        if await self._is_untracked(log.user_id):
            # 用藥者關掉提醒＝不追蹤他的服藥（見 `_is_untracked`）。搶佔已經把
            # 這筆改成 missed，這裡只能擋住推播；正常路徑會在搶佔之前就把紀錄
            # 註銷（見 process_ticks 階段 3），這裡是直接呼叫時的後盾。
            logger.info(
                "[MedicationScheduler] patient %s opted out of reminders; "
                "not alerting family for %s",
                log.user_id,
                log.id,
            )
            return DispatchOutcome.SKIPPED

        recipients = await self._family_recipients(log.user_id)
        if recipients is None:
            return DispatchOutcome.RETRY
        if not recipients:
            return DispatchOutcome.SKIPPED

        patient_name = await self._resolve_patient_name(log.user_id)
        # 藥名查表與 T+0／T+20 共用同一套機制（見 _TickMedicationNameCache）：
        # 家屬警報同樣是一個 tick 內可能有多筆，逐筆查「規則→藥品」沒有道理。
        medication_names = await medication_cache.get(log)

        failures: list[SendResult] = []
        delivered = False
        for member_id in recipients:
            # 語言、字級與通知意願都取收件人自己的設定。用藥者關掉自己的提醒
            # 不影響家屬收不收得到逾時通報，反之亦然。
            prefs = await self._resolve_prefs(member_id)
            if not prefs.notify_family:
                # 抑制的只有推播。`claim_caregiver_alert` 在搶佔的同一次更新裡就
                # 已把 status 設為 missed，因此紀錄仍然正確——家屬事後在 LIFF 上
                # 看得到這個時段錯過了，只是當下不會被推播打擾。
                logger.info(
                    "[MedicationScheduler] caregiver %s opted out of family alerts; "
                    "skipping %s",
                    member_id,
                    log.id,
                )
                continue
            alert_flex = build_caregiver_alert_flex(
                patient_name=patient_name,
                slot_type=log.slot_type,
                scheduled_time=to_taipei_hm(log.scheduled_at, default="08:00"),
                medication_names=medication_names,
                language=prefs.language,
                font_size=prefs.font_size,
            )
            result = await self._push_result(member_id, alert_flex)
            if result.ok:
                delivered = True
            else:
                failures.append(result)

        if delivered:
            return DispatchOutcome.DELIVERED
        if not failures:
            return DispatchOutcome.SKIPPED
        return _worst_case_for_retry(failures)

    async def _family_recipients(self, patient_id: str) -> Optional[list[str]]:
        """用藥逾時通報的家屬名單；用藥者本人恆不在其中。判定失敗回 None。

        名單來自 `FamilyAuthorizationService.notification_recipients`，與高風險
        藥物、緊急通報、掛號提醒是同一個 resolver，這裡不另外判斷誰算家屬。

        失敗時不能學掛號提醒退成空名單：掛號除了 T+30 還有送本人的階段，這裡的
        T+30 只送家屬，退成空名單等於靜靜吞掉一則通報。回 None 讓呼叫端把推播權
        還回去，下一個 tick 重試。
        """
        if self._authorization_service is None:
            logger.warning(
                "%s 沒有家庭授權服務，無法判定家屬名單，本則不送", self.LOG_PREFIX
            )
            return []
        try:
            recipients = await self._authorization_service.notification_recipients(
                patient_id, NOTIFICATION_KIND
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s 家屬名單判定失敗：%s", self.LOG_PREFIX, type(exc).__name__
            )
            return None
        unique: list[str] = []
        for uid in recipients or []:
            if uid and uid != patient_id and uid not in unique:
                unique.append(uid)
        return unique

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

    async def _notify_missed_summary(self, misfired_logs: list[MedicationLog]) -> None:
        """
        把本次 tick 新發現的錯過時段，依家屬彙整成一則通知送出。

        不做推播權搶佔：來源是 `upsert_log` 回報的 created 旗標，而 (reminder_id,
        scheduled_at) 唯一索引保證同一個時段只會被插入一次，所以多實例並存時也只有
        真正插入成功的那個實例會拿到這些 log。

        送不出去就只記 log，不重試：這是中斷後的補充告知，為它額外維護一份「待通知」
        狀態並不划算，而錯過的時段本身已經以 status=missed 留在資料庫裡。
        """
        name_cache: dict[str, str] = {}

        # 收件人與 T+30 逾時通報同一份名單，依收件人分組：一位家屬照顧兩位長輩
        # 時仍然只收一則。名單判定失敗的那位長輩就略過——這是補充告知，不重試。
        recipients_by_patient: dict[str, Optional[list[str]]] = {}
        by_recipient: dict[str, list[MedicationLog]] = {}
        for log in misfired_logs:
            if log.user_id not in recipients_by_patient:
                recipients_by_patient[log.user_id] = await self._family_recipients(
                    log.user_id
                )
            for member_id in recipients_by_patient[log.user_id] or []:
                by_recipient.setdefault(member_id, []).append(log)

        for caregiver_id, logs in by_recipient.items():
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

    async def process_ticks(self, now: Optional[datetime] = None) -> None:
        """執行一次排程檢查 (可代入特定的 now 時間供單元測試或實機測試)"""
        current_time = now or datetime.now(TAIPEI_TZ)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=TAIPEI_TZ)

        # 每個 tick 重新讀偏好、重新計算額度用完的則數（見 `_resolve_prefs`、
        # `_dispatch` 的 QUOTA 分支）。
        self._prefs_cache = {}
        self._quota_blocked = 0

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

        # 本次 tick 才發現的錯過時段，稍後依家屬彙整成一則通知。
        misfired_logs: list[MedicationLog] = []
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
                # T+20 催促與 T+30 家屬警報改以條目中最晚的時刻
                # （timeout_anchor_time）起算，不是 scheduled_dt（最早條目時刻）
                # ——飯前 07:30、飯後 08:30 時，08:00 不該催、08:00 更不該通知
                # 家屬漏吃（design 決策 2、4；spec「三階遞進推播」）。單一 none
                # 條目的舊規則兩個時刻相等，這裡算出來與變更前完全相同。
                anchor_dt = datetime.strptime(
                    f"{today_date_str} {reminder.timeout_anchor_time}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=current_time.tzinfo)
                urgent_at = anchor_dt + timedelta(minutes=URGENT_AFTER_ANCHOR_MINUTES)
                timeout_dt = anchor_dt + timedelta(minutes=CAREGIVER_ALERT_AFTER_ANCHOR_MINUTES)

                # 不為「提醒建立／重新啟用之前」的時段補建 log。
                # 否則 20:00 新增一筆早上 08:00 的提醒，會在同一個 tick 內連續
                # 觸發首刷提醒、T+20 催促、以及 T+30 家屬逾時警報（全是假的）。
                # 重新啟用是同一件事：上週建的提醒今天 20:00 重新打開（LIFF 開關、
                # 或藥袋提交復活一筆停用的規則），早上 08:00 一樣不該被補成漏服。
                # 沒有 enabled_at 的舊規則退回只看 created_at。
                activation_floor = ensure_aware_utc(reminder.created_at)
                if reminder.enabled_at is not None:
                    activation_floor = max(
                        activation_floor, ensure_aware_utc(reminder.enabled_at)
                    )
                if scheduled_dt < activation_floor:
                    continue

                # 關掉「用藥提醒通知」的人不追蹤（見 `_is_untracked`）：不建紀錄，
                # 三個階段就沒有東西可推、可判漏服、可通知家屬。放在 created_at
                # 判斷之後，被那一條擋掉的規則不必查 profile。
                if await self._is_untracked(reminder.user_id):
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
                    urgent_at=urgent_at,
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
                    misfired_logs.append(saved_log)
            except Exception:
                logger.exception(
                    f"[MedicationScheduler] Failed to upsert T+0min log for user {reminder.user_id}"
                )

        # 1b. 中斷期間錯過的時段：每位家屬彙整成一則通知（措辭與 T+30 逾時警報不同）
        if misfired_logs:
            await self._notify_missed_summary(misfired_logs)

        # 2. 使用 list_pending_patient_reminders 查詢已到期 (scheduled_at <= current_time) 且未發送首刷的紀錄推播
        pending_initial_logs = await self._log_repository.list_pending_patient_reminders(
            threshold_time=current_time
        )
        # 這批 log 共用同一份藥名查表（見 _TickMedicationNameCache）：許多使用者共用
        # 同一個時段（例如 08:00）時，藥名查詢不會隨 log 數量線性增加。這裡只是建立
        # 查表物件本身（不發查詢），迴圈與 _dispatch 的搶佔／推播流程完全不變。
        initial_medication_cache = self._medication_cache(pending_initial_logs)
        # 用藥提醒拉霸的歷史每一輪只讀一次、彙整成計數表，給這一輪所有 T+0 共用；
        # 沒有要送的就不讀。
        variant_stats = await self._load_variant_stats() if pending_initial_logs else None
        for log in pending_initial_logs:
            if await self._is_untracked(log.user_id):
                await self._cancel_untracked_log(log, "T+0min initial reminder")
                continue
            await self._dispatch(
                stage="T+0min initial reminder",
                log_id=log.id,
                claim=self._log_repository.claim_patient_reminder,
                release=self._log_repository.release_patient_reminder,
                send=partial(
                    self._send_patient_reminder,
                    log,
                    initial_medication_cache,
                    variant_stats,
                    current_time,
                ),
                **self._stage_hooks("patient_reminder"),
            )

        # ── 階段 2：T+20min 第二次溫馨催促 ─────────────────────────────
        # 門檻計算：20 分鐘的位移已經下放到展開時寫入的 urgent_at（見上方
        # 階段 1，= timeout_anchor_time + 20 分鐘），這裡直接傳現在時刻；
        # 沒有 urgent_at 的既有紀錄由 repository 端的 $or 分支退回
        # scheduled_at + 20min（等同本變更前的算法，見
        # list_pending_urgent_reminders 的說明，design 決策 4）。
        # 採用 $lte (小於等於) 能有效容忍執行秒數偏差或伺服器重啟延遲，絕不漏發，
        # 配合 urgent_reminder_sent 標記與單一 Document 狀態變更，保證不會重複發送。
        pending_urgent_logs = await self._log_repository.list_pending_urgent_reminders(
            threshold_time=current_time
        )
        urgent_medication_cache = self._medication_cache(pending_urgent_logs)
        for log in pending_urgent_logs:
            if await self._is_untracked(log.user_id):
                await self._cancel_untracked_log(log, "T+20min urgent reminder")
                continue
            await self._dispatch(
                stage="T+20min urgent reminder",
                log_id=log.id,
                claim=self._log_repository.claim_patient_urgent_reminder,
                release=self._log_repository.release_patient_urgent_reminder,
                send=partial(self._send_urgent_reminder, log, urgent_medication_cache),
                **self._stage_hooks("urgent_reminder"),
            )

        # ── 階段 3：T+30min 第三次家屬逾時警報 ─────────────────────────
        # 門檻計算：找出 timeout_at <= 當前時間 且狀態仍為 pending (未確認用藥) 的記錄
        pending_alert_logs = await self._log_repository.list_pending_caregiver_alerts(
            threshold_time=current_time
        )
        alert_medication_cache = self._medication_cache(pending_alert_logs)
        for log in pending_alert_logs:
            # 在搶佔之前擋：搶佔會把 status 改成 missed，不追蹤的人不該留下漏服。
            if await self._is_untracked(log.user_id):
                await self._cancel_untracked_log(log, "T+30min caregiver alert")
                continue
            await self._dispatch(
                stage="T+30min caregiver alert",
                log_id=log.id,
                claim=self._log_repository.claim_caregiver_alert,
                release=self._log_repository.release_caregiver_alert,
                send=partial(self._send_caregiver_alert, log, alert_medication_cache),
                **self._stage_hooks("caregiver_alert"),
            )

        if self._quota_blocked:
            # 一個 tick 一行，不逐筆：額度用完的那個月每分鐘都會走到這裡。
            logger.warning(
                "[MedicationScheduler] 本 tick 因 LINE 額度用完，%d 則提醒未送出；"
                "推播權已還回、不計入重試次數，額度恢復後下一個 tick 會補送",
                self._quota_blocked,
            )

    def _stage_hooks(self, stage: str) -> dict[str, Callable[..., Any]]:
        """`_dispatch` 三個結果回呼的綁定：送達／略過寫時刻標記，明確拒絕立刻
        放棄（見 MedicationLogRepository 的「推播權租約」段落）。"""
        repo = self._log_repository
        return {
            "mark_sent": partial(repo.mark_stage_sent, stage=stage),
            "mark_skipped": partial(repo.mark_stage_skipped, stage=stage),
            "give_up": partial(repo.give_up_stage, stage=stage),
        }



def start_medication_scheduler(
    *,
    enabled: bool = True,
    replier: LineReplier,
    user_profile_service: Optional[UserProfileService] = None,
    authorization_service: Any = None,
) -> Optional[MedicationScheduler]:
    if not enabled:
        logger.info("[MedicationScheduler] disabled")
        return None

    scheduler = MedicationScheduler(
        replier=replier,
        user_profile_service=user_profile_service,
        authorization_service=authorization_service,
    )
    scheduler.start()
    return scheduler
