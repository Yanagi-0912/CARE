"""
判斷院所當下的營業狀態，並算出下一次開診時間。

為什麼這段邏輯值得獨立成一個模組：使用者真正在問的不是「這家現在有開嗎」，
而是「我什麼時候能去」。前者是一個 bool，後者需要狀態分級（營業中／午休中／
今日已結束）加上跨日跨週的下次開診計算，還得處理兩個資料上的陷阱（見下）。
原本這段邏輯寫在 Flex 訊息模組裡，隨著需求擴張已不適合留在呈現層。

兩個資料陷阱，決定了本模組的兩條特殊規則：

1. **clinicTime 記的是門診時間，不是「有沒有人」。** 實測 197 家設有急診醫學科的院所，
   在深夜 03:00 依 clinicTime 判定為營業中者僅 1 家 —— 因為記錄的是門診 08:00–17:00。
   因此設有急診的院所一律標「設有急診」而非「休診」，且不得因營業狀態被篩掉。
   刻意**不宣稱「24 小時」**：資料只說有急診科別，沒說開放時間，宣稱時間就是編造。

2. **clinicTime 不知道春節。** 1,304 家院所的 notes 寫著節慶特殊開診（如
   「春節假期2／17~2／22休診」），其中 617 家的 clinicTime 仍判定為營業中。
   但這類註記綁定特定日期，若全年將它們降級成「請電洽」，標籤就失去意義了。
   因此以「有無日期樣式」分兩層：含日期的只顯示原文，不含日期的長期性註記才降級。
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from app.schemas import ClinicDaySchedule, MedicalFacility

TAIPEI_TZ = timezone(timedelta(hours=8))

# 與 datetime.weekday() 對齊：星期一為 0
WEEKDAY_KEYS: tuple[str, ...] = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)

EMERGENCY_DEPARTMENT_KEYWORD = "急診"

# 長期性註記的判定：提及休診又不綁定日期，才代表「平常就要先問」
_CLOSURE_KEYWORDS_RE = re.compile(r"休診|停診|電話洽詢|先電洽|預約")

# 日期樣式（含民國年「115／01／01」與半形「2/17」）。命中即視為綁定特定日期，
# 只顯示原文、不動狀態標籤 —— 八月不該因為元旦的註記而永久降級。
_DATE_PATTERN_RE = re.compile(r"\d+\s*[／/]\s*\d+")

# 下次開診最多往後找幾天（含今天）。七天內都沒有就是真的沒有排班資料。
_NEXT_OPEN_LOOKAHEAD_DAYS = 7


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
    """請電洽 —— 有長期性註記表明需先聯繫。"""

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
    """院所是否設有急診醫學科。容忍 departments 為髒資料（整串擠在單一元素）。"""
    for item in facility.departments or []:
        if item and EMERGENCY_DEPARTMENT_KEYWORD in str(item):
            return True
    return False


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
        3. 七天皆無時段                → UNKNOWN
        4. 今日 isClosed               → CLOSED_DAY
        5. 當下在時段內                → OPEN
        6. 今日還會開，但還沒開過      → BEFORE_OPEN
        7. 今日還會開，且已有時段結束  → BREAK
        8. 其他                        → CLOSED_TODAY
    """
    moment = now or datetime.now(TAIPEI_TZ)
    clinic_time = facility.clinic_time
    note = facility.notes or None

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

    # 3. 完全沒有排班資料 —— 這是「不知道」，不是「沒開」。
    if not _has_any_slot(clinic_time):
        return BusinessHoursResult(status=BusinessStatus.UNKNOWN, note=note)

    next_open = find_next_open(clinic_time, moment)
    today_key = WEEKDAY_KEYS[moment.weekday()]
    today = _day_schedule(clinic_time, today_key)

    # 4. 今日公休
    if today is None or today.isClosed:
        return BusinessHoursResult(
            status=BusinessStatus.CLOSED_DAY, next_open=next_open, note=note
        )

    current_time_text = moment.strftime("%H:%M")
    today_slots = _sorted_slots(today)

    # 5. 當下在某個時段內
    for open_text, close_text in today_slots:
        if open_text <= current_time_text <= close_text:
            return BusinessHoursResult(status=BusinessStatus.OPEN, note=note)

    # 6/7/8. 今日還會開 → 分「尚未開診」與「午休中」；今日不會再開 → 已結束。
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
