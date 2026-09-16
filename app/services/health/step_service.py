"""計步 (``step_sessions``) 的服務層（step-counter spec「同步的冪等性」
「日期歸屬」「合理性檢查」；constraints.md「Steps」）。

授權判定不在這裡——同其餘 health 服務的慣例，由
``app/routers/users/health.py`` 直接處理：只有本人可以寫入（任何操作者與
資料本人不同一律 403，包含 GUARDIAN，見 spec「只有本人可以寫入」），他人
讀取需 SENSITIVE READ。這裡只負責計步本身的業務規則：

- 冪等性：前端回報的是工作階段**目前的累計值**，SHALL NOT 是增量；每個
  工作階段保留收過的最大值，交給 ``StepSessionRepository.sync_progress``
  的 ``$max`` 語意（見該檔案），這裡不重複。
- 日期歸屬：工作階段的日期是**該工作階段第一次同步時**帶的 ``started_at``
  換算成台北時區的日曆日；同一個工作階段之後每次同步即使帶了不同的
  ``started_at``，也 SHALL 被忽略，一律沿用第一次的值（dispatch notes）。
- 合理性檢查：自工作階段開始（沿用上面「第一次」的 ``started_at``，不是
  這次請求帶的值）起算，平均每秒超過 5 步 SHALL 被拒絕（422）且 SHALL NOT
  寫入——因此在呼叫 repository 的 upsert 之前，先讀一次既有工作階段
  （``get_session``，只讀不寫）來判斷，通過才呼叫 ``sync_progress``。
- 每日步數：新增／查詢都直接借用 repository 的資料庫端加總，不在這裡
  重算。

時鐘以 ``clock`` 注入（同 ``app/services/appointment/appointment_service.py``
的 ``AppointmentService`` 慣例）：合理性檢查與查詢預設區間都需要控制
「現在」的邊界，專案禁止 ``unittest.mock.patch``／monkeypatch 全域時間。
"""

from datetime import date, datetime, timedelta, timezone
from typing import Callable, List, Optional

from fastapi import HTTPException

from app.models.health import TAIPEI_TZ, StepCount, StepSessionSyncRequest
from app.repositories.step_session_repository import StepSessionRepository

# 自工作階段開始起算，平均每秒不得超過 5 步（step-counter spec「合理性
# 檢查」）；恰好等於 5 SHALL 被接受，只有超過才拒絕。
MAX_STEPS_PER_SECOND = 5

# 查詢區間：兩者皆省略時預設最近 7 天（含今天，台北時區）；區間長度上限
# 90 天，超過視為打錯字（dispatch notes「GET /api/health/steps」）。
DEFAULT_QUERY_RANGE_DAYS = 7
MAX_QUERY_RANGE_DAYS = 90

UNREASONABLE_RATE_DETAIL = "步數異常：平均速度超過合理範圍，請確認裝置或重新開始計步。"
RANGE_TOO_LONG_DETAIL = f"查詢區間不得超過 {MAX_QUERY_RANGE_DAYS} 天。"
START_AFTER_END_DETAIL = "起始日期不得晚於結束日期。"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """未帶時區的 datetime 視為 UTC（同 ``app/models/health.py`` 的慣例：
    Motor client 未啟用 tz_aware，時間經資料庫存取一圈後會變成 naive
    UTC）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class StepService:
    """``step_sessions``／每日步數彙總的讀寫。repository 以類別（或相同
    介面的假物件）注入，方便測試換成假的（同專案其餘 health 服務的慣例）。
    """

    def __init__(
        self,
        repository: type[StepSessionRepository] = StepSessionRepository,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._repository = repository
        self._clock = clock

    async def sync(
        self, user_id: str, session_id: str, request: StepSessionSyncRequest
    ) -> StepCount:
        """同步一次工作階段的累計步數。``user_id`` 已由呼叫端（router）
        確認就是操作者本人——步數沒有代為回報（spec「只有本人可以寫入」）。
        """
        now = self._clock()

        existing = await self._repository.get_session(user_id, session_id)
        if existing is not None:
            # 既有工作階段：日期與 started_at 一律沿用第一次同步時存下的
            # 值，即使這次請求帶了不同的 started_at 也忽略（dispatch
            # notes）。
            effective_started_at = _as_utc(existing.started_at)
        else:
            effective_started_at = _as_utc(request.started_at)

        # Task 6 修復：`started_at`（``StepSessionSyncRequest`` 已容許最多
        # 領先送出當下 5 分鐘，見該模型的 `_validate_started_at`）領先伺服器
        # 現在時間時，跳過合理性檢查而不是硬算。這不是罕見情境——手機時鐘
        # 比伺服器快幾分鐘很常見；`elapsed_seconds` 原本用 `max(..., 1.0)`
        # 墊底，領先時真實經過時間其實是負的、根本量不出來，卻被硬夾成 1
        # 秒去算「每秒幾步」，等於拿一個假造的極小分母去除，隨便走幾步都會
        # 超過 5 步/秒而被 422——一支快 3 分鐘的手機在最初幾分鐘內的每一次
        # 同步都會被擋下。這個檢查本來就是抓打錯字／異常上傳，不是安全邊界
        # （spec「合理性檢查」是 typo guard，不是資安控制），時鐘偏移造成
        # 「經過時間量不出來」時就不該再拿它來擋——步數是累計值，這裡放行
        # 也不會遺失或重複計數，等 `now` 追上 `started_at` 之後，之後的同步
        # 自然會回到正常的速率檢查。
        if effective_started_at <= now:
            elapsed_seconds = max((now - effective_started_at).total_seconds(), 1.0)
            if request.steps / elapsed_seconds > MAX_STEPS_PER_SECOND:
                raise HTTPException(status_code=422, detail=UNREASONABLE_RATE_DETAIL)

        date_str = effective_started_at.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d")

        await self._repository.sync_progress(
            user_id=user_id,
            session_id=session_id,
            date_str=date_str,
            steps=request.steps,
            started_at=effective_started_at,
            now=now,
        )
        return await self._repository.get_daily_total(user_id, date_str)

    async def list_daily_totals(
        self,
        user_id: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> List[StepCount]:
        """查詢台北日曆日區間內的每日步數；兩者皆省略時預設最近 7 天
        （含今天）。只回傳有工作階段的日期（沒有工作階段的日期前端視為
        無資料，不是 0——見 repository），新到舊排序。
        """
        if end is None:
            end = self._clock().astimezone(TAIPEI_TZ).date()
        if start is None:
            start = end - timedelta(days=DEFAULT_QUERY_RANGE_DAYS - 1)

        if start > end:
            raise HTTPException(status_code=422, detail=START_AFTER_END_DETAIL)
        if (end - start).days > MAX_QUERY_RANGE_DAYS:
            raise HTTPException(status_code=422, detail=RANGE_TOO_LONG_DETAIL)

        return await self._repository.list_daily_totals(
            user_id, start.isoformat(), end.isoformat()
        )
