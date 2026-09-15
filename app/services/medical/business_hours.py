"""
判斷院所當下的營業狀態，並算出下一次開診時間。

為什麼這段邏輯值得獨立成一個模組：使用者真正在問的不是「這家現在有開嗎」，
而是「我什麼時候能去」。前者是一個 bool，後者需要狀態分級（營業中／午休中／
今日已結束）加上跨日跨週的下次開診計算，還得處理三個資料上的陷阱（見下）。
原本這段邏輯寫在 Flex 訊息模組裡，隨著需求擴張已不適合留在呈現層。

三個資料陷阱，決定了本模組的三條特殊規則：

1. **clinicTime 記的是門診時間，不是「有沒有人」。** 實測 197 家設有急診醫學科的院所，
   在深夜 03:00 依 clinicTime 判定為營業中者僅 1 家 —— 因為記錄的是門診 08:00–17:00。
   因此設有急診的院所一律標「設有急診」而非「休診」，且不得因營業狀態被篩掉。
   刻意**不宣稱「24 小時」**：資料只說有急診科別，沒說開放時間，宣稱時間就是編造。
   但只認醫院與衛生所：列了急診醫學科的一般診所，門診時間都在白天
   （見 has_emergency_department）。

2. **clinicTime 不知道春節。** 1,304 家院所的 notes 寫著節慶特殊開診（如
   「春節假期2／17~2／22休診」），其中 617 家的 clinicTime 仍判定為營業中。
   但這類註記綁定特定日期，若全年將它們降級成「請電洽」，標籤就失去意義了。
   因此以「有無日期樣式」分兩層：含日期的只顯示原文，不含日期的長期性註記才降級。

3. **凌晨開始的時段多半是資料錯誤。** 週三 02:30 依 clinicTime 判定營業中的 32 家，
   都是靠一段凌晨開始的時段。有這種時段的 189 家裡，142 家只有一週中的某一天有
   （例如週三 00:00–18:00，週一卻是 09:00–12:00）—— 是資料錯誤，不是夜間門診。
   它的前半段常落在前一天：週二開到 24:00，週三又從 00:00 接下去。
   因此當天有 06:00 前開始的時段，或當天開到午夜而隔天從凌晨接下去，
   就不採信當天的時段，改標「請電洽」。
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from app.schemas import ClinicDaySchedule, MedicalFacility

TAIPEI_TZ = timezone(timedelta(hours=8))

# 與 datetime.weekday() 對齊：星期一為 0
WEEKDAY_KEYS: tuple[str, ...] = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)

EMERGENCY_DEPARTMENT_KEYWORD = "急診"

# 設有急診只認醫院與衛生所。departments 列了急診醫學科的 199 家裡有 9 家是一般診所，
# clinicTime 都是一般門診時段（例如 14:00–17:00），凌晨照「設有急診」列出來，
# 使用者會跑到一間關著的診所。另外 2 家衛生所都在離島（蘭嶼、望安），寧可列出來。
_EMERGENCY_PROVIDER_TYPE_KEYWORD = "醫院"
_EMERGENCY_PROVIDER_NAME_KEYWORD = "衛生所"

# has_emergency_department 的 Mongo 版本，兩邊的條件必須一致（見 build_open_now_query）。
_EMERGENCY_PROVIDER_QUERY: dict[str, Any] = {
    "departments": {"$regex": EMERGENCY_DEPARTMENT_KEYWORD},
    "$or": [
        {"type": {"$regex": _EMERGENCY_PROVIDER_TYPE_KEYWORD}},
        {"name": {"$regex": _EMERGENCY_PROVIDER_NAME_KEYWORD}},
    ],
}

# 長期性註記的判定：提及休診又不綁定日期，才代表「平常就要先問」
_CLOSURE_KEYWORDS_RE = re.compile(r"休診|停診|電話洽詢|先電洽|預約")

# 日期樣式（含民國年「115／01／01」與半形「2/17」）。命中即視為綁定特定日期，
# 只顯示原文、不動狀態標籤 —— 八月不該因為元旦的註記而永久降級。
_DATE_PATTERN_RE = re.compile(r"\d+\s*[／/]\s*\d+")

# 下次開診最多往後找幾天（含今天）。七天內都沒有就是真的沒有排班資料。
_NEXT_OPEN_LOOKAHEAD_DAYS = 7

# 門診時段開始時間的可信下限（見模組說明第 3 點）。06:00 前開始的時段共 346 段，
# 286 段是 00:00，其餘是 02:00、04:50～04:56 這類；06:30、07:00 開始的分別有
# 82、1,325 段，才是正常的早診。
_EARLIEST_CREDIBLE_OPEN = "06:00"

# 開到這個時間以後、隔天又從凌晨接下去，視為同一筆跨夜錯誤資料的前半段。
# 套用凌晨規則後，週二 23:30 仍判定營業中的 26 家裡有 23 家是這樣，其中 22 家
# 那段時段在週一、四、五都沒有（例如週二 14:00–24:00、週一 14:00–17:30）；
# 剩下 3 家（例如每天 09:00–24:00 的醫院）隔天正常時間才開，照常採信。
_MIDNIGHT_CLOSE = "23:59"

# 深夜：23:00 起到 06:00 前。套用上面兩條規則後，全台照門診時間在營業的非急診院所，
# 23:00 只剩 18–21 家（週二、週六實測），23:15 以後 2–3 家，05:00–05:30 是 0 家。
# 06:00 以後多數診所一兩個小時內就開門，跟午休一樣，列最近的院所和下次開診時間仍然有用。
_LATE_NIGHT_START = "23:00"
_LATE_NIGHT_END = "06:00"


class BusinessStatus(Enum):
    """院所當下的營業狀態。優先序見 resolve_business_hours。"""

    OPEN = "open"
    """營業中。"""

    BEFORE_OPEN = "before_open"
    """今日尚未開診 —— 今日有排班，但第一個時段還沒到。"""

    BREAK = "break"
    """午休中 —— 今日已有時段結束，且尚有後續時段。"""

    CLOSED_TODAY = "closed_today"
    """今日已結束 —— 今日有排班但已過最後一個時段。"""

    CLOSED_DAY = "closed_day"
    """今日休診（isClosed）。"""

    EMERGENCY = "emergency"
    """設有急診。豁免營業時間判斷，且不得因狀態被篩除。"""

    CALL_AHEAD = "call_ahead"
    """請電洽 —— 有長期性註記表明需先聯繫，或今日的時段資料不可信。"""

    UNKNOWN = "unknown"
    """無營業時間資料可判斷。不可與「休診」混淆。"""


@dataclass(frozen=True)
class NextOpen:
    """下一次開診的時間點。"""

    weekday_key: str
    """星期英文小寫，對應 WEEKDAY_KEYS。"""

    time_text: str
    """開診時間，例如 "08:00"。"""

    is_today: bool
    """是否為今日稍後開診，供呈現層決定要不要顯示星期。"""


@dataclass(frozen=True)
class BusinessHoursResult:
    """一次營業狀態判斷的完整結果。"""

    status: BusinessStatus

    next_open: NextOpen | None = None
    """下次開診時間；營業中或七天內查無排班時為 None。"""

    note: str | None = None
    """院所 notes 原文。無論是否影響狀態標籤都一律回傳供顯示。"""

    @property
    def is_open_now(self) -> bool:
        """是否視為「現在可前往」。急診豁免視為可前往，因其本質即為緊急就診。"""
        return self.status in (BusinessStatus.OPEN, BusinessStatus.EMERGENCY)

    @property
    def is_emergency(self) -> bool:
        """是否為急診豁免。篩選邏輯以此判斷「不得排除」，與狀態文案解耦。"""
        return self.status is BusinessStatus.EMERGENCY


def has_emergency_department(facility: MedicalFacility) -> bool:
    """
    院所是否設有急診。容忍 departments 為髒資料（整串擠在單一元素）。

    只認醫院與衛生所（見 _EMERGENCY_PROVIDER_TYPE_KEYWORD）：這個判斷決定凌晨
    能不能把院所列出來，而列了急診醫學科的一般診所凌晨是關的。
    """
    if (
        _EMERGENCY_PROVIDER_TYPE_KEYWORD not in (facility.type or "")
        and _EMERGENCY_PROVIDER_NAME_KEYWORD not in (facility.name or "")
    ):
        return False
    for item in facility.departments or []:
        if item and EMERGENCY_DEPARTMENT_KEYWORD in str(item):
            return True
    return False


def build_open_now_query(now: datetime) -> dict[str, Any]:
    """
    「現在可前往」的 Mongo 查詢條件，給 $geoNear 的 query 用：Mongo 由近到遠掃、
    邊掃邊篩，50 公里內最近的幾家有開的就會先回來。

    星期幾在查詢當下就知道，所以只是對 clinicTime.<星期>.slots 做 $elemMatch，
    用不到 $expr。長期性註記、跨夜的錯誤時段這類規則寫成查詢條件不划算，仍由呼叫端用
    resolve_business_hours 做最後判斷 —— 本條件因此必須是「營業中或急診」的
    超集合：寧可多撈幾家回去被濾掉，不能漏掉任何一家。
    """
    weekday_key = WEEKDAY_KEYS[now.weekday()]
    current_time_text = now.strftime("%H:%M")
    in_clinic_hours = {
        f"clinicTime.{weekday_key}.isClosed": {"$ne": True},
        f"clinicTime.{weekday_key}.slots": {
            "$elemMatch": {
                "open": {"$gte": _EARLIEST_CREDIBLE_OPEN, "$lte": current_time_text},
                "close": {"$gte": current_time_text},
            }
        },
    }
    return {"$or": [_EMERGENCY_PROVIDER_QUERY, in_clinic_hours]}


def is_late_night(now: datetime) -> bool:
    """
    現在是否為深夜 —— 附近幾乎沒有門診在開，列最近的院所等於列一排明天才開的。
    呼叫端據此決定使用者沒明說時，要不要只列現在能去的。
    """
    current_time_text = now.strftime("%H:%M")
    return current_time_text >= _LATE_NIGHT_START or current_time_text < _LATE_NIGHT_END


def resolve_clinic_hours(facility: MedicalFacility) -> BusinessHoursResult:
    """
    取得「門診」的營業狀態 —— 也就是把急診豁免拿掉之後，這家院所現在到底開不開。

    為什麼要有這一層：resolve_business_hours 讓急診壓過一切狀態（見該函式的優先序），
    這對「能不能去」的篩選是對的，但對呈現是不夠的 —— 一家設有急診的醫院門診
    可能正在午休，使用者需要同時看到「設有急診」與「午休中」兩件事。把 departments
    清空後重跑，得到的就是純粹的門診狀態，急診則由 has_emergency_department 另外標示。

    原本這段寫在 Flex 訊息模組裡，LIFF 的 REST API 也要用同一套判斷，
    留在呈現層等於逼第二個通道複製一份。
    """
    if not has_emergency_department(facility):
        return resolve_business_hours(facility)
    return resolve_business_hours(facility.model_copy(update={"departments": None}))


def _is_date_bound_note(note: str) -> bool:
    """註記是否綁定特定日期（如「春節假期2／17~2／22休診」）。"""
    return bool(_DATE_PATTERN_RE.search(note))


def _requires_call_ahead(note: str | None) -> bool:
    """
    註記是否代表「平常就得先聯繫」。

    只有不綁定日期的註記才算 —— 否則八月會因為元旦的休診註記而顯示請電洽。
    """
    if not note:
        return False
    if _is_date_bound_note(note):
        return False
    return bool(_CLOSURE_KEYWORDS_RE.search(note))


def _day_schedule(
    clinic_time: dict[str, ClinicDaySchedule] | None, weekday_key: str
) -> ClinicDaySchedule | None:
    if not clinic_time:
        return None
    return clinic_time.get(weekday_key)


def _sorted_slots(day: ClinicDaySchedule | None) -> list[tuple[str, str]]:
    """回傳當日已排序且欄位完整的時段，格式為 (open, close)。"""
    if day is None or day.isClosed:
        return []
    slots = [
        (slot.open, slot.close)
        for slot in day.slots
        if slot.open and slot.close
    ]
    return sorted(slots)


def _starts_before_dawn(day: ClinicDaySchedule | None) -> bool:
    return any(
        open_text < _EARLIEST_CREDIBLE_OPEN for open_text, _close in _sorted_slots(day)
    )


def _is_implausible_day(
    day: ClinicDaySchedule | None, next_day: ClinicDaySchedule | None
) -> bool:
    """
    當天的時段資料是否不可信（見模組說明第 3 點）：當天有凌晨開始的時段，
    或當天開到午夜、隔天又從凌晨接下去。
    """
    if _starts_before_dawn(day):
        return True
    runs_to_midnight = any(
        close_text >= _MIDNIGHT_CLOSE for _open, close_text in _sorted_slots(day)
    )
    return runs_to_midnight and _starts_before_dawn(next_day)


def _has_any_slot(clinic_time: dict[str, ClinicDaySchedule] | None) -> bool:
    """七天內是否有任何一個可用時段。全無代表無資料，而非休診。"""
    if not clinic_time:
        return False
    return any(
        _sorted_slots(_day_schedule(clinic_time, key)) for key in WEEKDAY_KEYS
    )


def find_next_open(
    clinic_time: dict[str, ClinicDaySchedule] | None, now: datetime
) -> NextOpen | None:
    """
    找出下一次開診時間，依序檢查今日稍後時段 → 明日 → 最多往後七天。

    凌晨開始的時段不算（見 _EARLIEST_CREDIBLE_OPEN）——「下次開診 週三 00:00」
    是把錯誤資料講給使用者聽。
    七天內查無任何時段則回 None（呈現層應省略下次開診資訊，而非顯示空值）。
    """
    if not clinic_time:
        return None

    current_time_text = now.strftime("%H:%M")

    for offset in range(_NEXT_OPEN_LOOKAHEAD_DAYS):
        weekday_index = (now.weekday() + offset) % 7
        weekday_key = WEEKDAY_KEYS[weekday_index]
        slots = _sorted_slots(_day_schedule(clinic_time, weekday_key))

        for open_text, _close_text in slots:
            if open_text < _EARLIEST_CREDIBLE_OPEN:
                continue
            # 今天只看還沒到的時段；之後的日子整天都算
            if offset == 0 and open_text <= current_time_text:
                continue
            return NextOpen(
                weekday_key=weekday_key,
                time_text=open_text,
                is_today=offset == 0,
            )

    return None


def resolve_business_hours(
    facility: MedicalFacility, now: datetime | None = None
) -> BusinessHoursResult:
    """
    判斷院所當下的營業狀態與下次開診時間。

    `now` 以參數注入而非在函式內取當前時間，測試才能固定時間點而不需 monkey patch。
    省略時取台灣時間。

    狀態優先序（急診置於最前，確保任何註記或時段狀況都不會使急診院所顯示為休診）：

        1. 設有急診                    → EMERGENCY
        2. 長期性註記                  → CALL_AHEAD
        3. 今日時段不可信（凌晨、跨夜）→ CALL_AHEAD
        4. 七天皆無時段                → UNKNOWN
        5. 今日 isClosed               → CLOSED_DAY
        6. 當下在時段內                → OPEN
        7. 今日還會開，但還沒開過      → BEFORE_OPEN
        8. 今日還會開，且已有時段結束  → BREAK
        9. 其他                        → CLOSED_TODAY
    """
    moment = now or datetime.now(TAIPEI_TZ)
    clinic_time = facility.clinic_time
    note = facility.notes or None
    today = _day_schedule(clinic_time, WEEKDAY_KEYS[moment.weekday()])
    tomorrow = _day_schedule(clinic_time, WEEKDAY_KEYS[(moment.weekday() + 1) % 7])

    # 1. 急診豁免。優先於一切，包含註記與時段。
    if has_emergency_department(facility):
        return BusinessHoursResult(
            status=BusinessStatus.EMERGENCY,
            next_open=find_next_open(clinic_time, moment),
            note=note,
        )

    # 2. 長期性註記：平常就得先聯繫，講具體時段反而誤導。
    if _requires_call_ahead(note):
        return BusinessHoursResult(status=BusinessStatus.CALL_AHEAD, note=note)

    # 3. 今日時段不可信。照它判斷，牙醫診所會在凌晨兩點半顯示營業中；
    #    講不準就請使用者先打電話，不猜哪一段才是真的。
    if _is_implausible_day(today, tomorrow):
        return BusinessHoursResult(status=BusinessStatus.CALL_AHEAD, note=note)

    # 4. 完全沒有排班資料 —— 這是「不知道」，不是「沒開」。
    if not _has_any_slot(clinic_time):
        return BusinessHoursResult(status=BusinessStatus.UNKNOWN, note=note)

    next_open = find_next_open(clinic_time, moment)

    # 5. 今日公休
    if today is None or today.isClosed:
        return BusinessHoursResult(
            status=BusinessStatus.CLOSED_DAY, next_open=next_open, note=note
        )

    current_time_text = moment.strftime("%H:%M")
    today_slots = _sorted_slots(today)

    # 6. 當下在某個時段內
    for open_text, close_text in today_slots:
        if open_text <= current_time_text <= close_text:
            return BusinessHoursResult(status=BusinessStatus.OPEN, note=note)

    # 7/8/9. 今日還會開 → 分「尚未開診」與「午休中」；今日不會再開 → 已結束。
    # 三者對使用者是不同的決定（再等等／下午再來／改天再來），不可混為「休診中」。
    # 「午休中」必須真的落在兩個時段之間 —— 凌晨三點是尚未開診，不是午休。
    if next_open is not None and next_open.is_today:
        already_ended = any(
            close_text < current_time_text for _open_text, close_text in today_slots
        )
        status = (
            BusinessStatus.BREAK if already_ended else BusinessStatus.BEFORE_OPEN
        )
    else:
        status = BusinessStatus.CLOSED_TODAY

    return BusinessHoursResult(status=status, next_open=next_open, note=note)
