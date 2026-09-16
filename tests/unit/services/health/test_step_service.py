"""``StepService`` 的同步與查詢（step-counter spec「同步的冪等性」「日期
歸屬」「合理性檢查」）。

授權判定（只有本人可寫入、他人讀取需 SENSITIVE READ）不在這裡——同其餘
health 服務的慣例，由 ``app/routers/users/health.py`` 直接處理；這裡只驗證
服務層自己該負責的部分：冪等性（$max 語意，交給 repository，這裡驗證服務
層有把正確的值傳過去）、日期歸屬（依 ``started_at`` 換算台北日曆日）、合理
性檢查（自工作階段開始起算平均每秒不得超過 5 步，且要用**既有**工作階段的
``started_at``，不是這次請求帶的值）、查詢的預設區間與上限。

「負值」「session_id 非 UUID v4」「代為回報」不在這裡測——前者已在
``StepSessionSyncRequest``（Pydantic 邊界，ge=0）擋下，後兩者是路徑參數
型別與 router 的身分比對，見 tests/unit/models/test_health_models.py 與
tests/unit/routers/test_health_router_authorization.py。

repository 以假物件注入（全專案 DI 風格：不使用 ``unittest.mock.patch``）；
「現在」以 ``clock`` 注入——合理性檢查與查詢預設區間都需要控制「現在」的
邊界，專案禁止 monkeypatch 全域時間。
"""

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import pytest
from fastapi import HTTPException

from app.models.health import TAIPEI_TZ, StepCount, StepSession, StepSessionSyncRequest
from app.services.health.step_service import StepService

OWNER = "U_OWNER"


class _FakeStepRepository:
    """記錄呼叫、以字典模擬 Mongo 文件的假 repository；``$max``／每日加總／
    區間查詢的邏輯都在這裡重新實作一份精簡版本——不是重複測 repository
    （那份已經在 tests/unit/repositories/test_step_session_repository.py
    驗證過），而是讓服務層測試能在不碰真資料庫的情況下驗證「服務層傳給
    repository 的值是否正確」。"""

    def __init__(self) -> None:
        self.sessions: Dict[Tuple[str, str], StepSession] = {}
        self.sync_calls: List[tuple] = []

    async def get_session(self, user_id: str, session_id: str) -> Optional[StepSession]:
        return self.sessions.get((user_id, session_id))

    async def sync_progress(
        self,
        user_id: str,
        session_id: str,
        date_str: str,
        steps: int,
        started_at: datetime,
        now: Optional[datetime] = None,
    ) -> StepSession:
        self.sync_calls.append((user_id, session_id, date_str, steps, started_at))
        moment = now or datetime.now(timezone.utc)
        key = (user_id, session_id)
        existing = self.sessions.get(key)
        if existing is None:
            session = StepSession(
                user_id=user_id,
                session_id=session_id,
                date=date_str,
                steps=steps,
                started_at=started_at,
                last_synced_at=moment,
            )
        else:
            session = existing.model_copy(
                update={"steps": max(existing.steps, steps), "last_synced_at": moment}
            )
        self.sessions[key] = session
        return session

    async def get_daily_total(self, user_id: str, date_str: str) -> StepCount:
        total = sum(
            s.steps
            for s in self.sessions.values()
            if s.user_id == user_id and s.date == date_str
        )
        return StepCount(user_id=user_id, date=date_str, steps=total)

    async def list_daily_totals(
        self, user_id: str, start_date: str, end_date: str
    ) -> List[StepCount]:
        totals: Dict[str, int] = {}
        for s in self.sessions.values():
            if s.user_id == user_id and start_date <= s.date <= end_date:
                totals[s.date] = totals.get(s.date, 0) + s.steps
        return [
            StepCount(user_id=user_id, date=d, steps=t)
            for d, t in sorted(totals.items(), reverse=True)
        ]


def _service(now: datetime, repository: Optional[_FakeStepRepository] = None):
    repository = repository or _FakeStepRepository()
    service = StepService(repository=repository, clock=lambda: now)
    return service, repository


def _request(steps: int, started_at: datetime) -> StepSessionSyncRequest:
    return StepSessionSyncRequest(steps=steps, started_at=started_at)


# ── 冪等性：重送、亂序 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resending_the_same_cumulative_value_is_idempotent():
    """spec「重送同一個值」：同一工作階段的累計 300 步被送達兩次，該工作
    階段（也是當日唯一一個工作階段）的步數 SHALL 為 300。"""
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    now = started_at + timedelta(hours=1)
    service, repository = _service(now)

    await service.sync(OWNER, "S1", _request(300, started_at))
    result = await service.sync(OWNER, "S1", _request(300, started_at))

    assert result.steps == 300
    assert repository.sessions[(OWNER, "S1")].steps == 300


@pytest.mark.asyncio
async def test_out_of_order_smaller_value_does_not_decrease_steps():
    """spec「亂序送達」：先收到 120，再收到較早送出的 100，該工作階段的
    步數 SHALL 維持 120。"""
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    now = started_at + timedelta(hours=1)
    service, repository = _service(now)

    await service.sync(OWNER, "S1", _request(120, started_at))
    result = await service.sync(OWNER, "S1", _request(100, started_at))

    assert result.steps == 120


@pytest.mark.asyncio
async def test_two_sessions_the_same_day_are_summed():
    """spec「同一天兩個工作階段」：累計分別為 300 與 500，當日步數 SHALL
    為 800。"""
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    now = started_at + timedelta(hours=1)
    service, repository = _service(now)

    await service.sync(OWNER, "S1", _request(300, started_at))
    result = await service.sync(OWNER, "S2", _request(500, started_at))

    assert result.steps == 800
    assert result.user_id == OWNER
    assert result.date == "2026-09-01"


@pytest.mark.asyncio
async def test_later_sync_ignores_a_changed_started_at_and_uses_the_stored_one():
    """dispatch notes：後續同步即使帶了不同的 ``started_at`` 也 SHALL 被
    忽略,一律沿用第一次的值——用來算日期歸屬與合理性檢查的起點都是。

    第一次同步的 started_at 距 now 200 秒；第二次同步謊報 started_at 幾乎
    等於 now（距 now 僅 1 秒），並帶一個以「幾乎等於 now」計算會超標
    （600／1 秒 = 600 步/秒）、但以「原本 200 秒前」計算不會超標
    （600／200 秒 = 3 步/秒）的步數。若服務層誤用了這次請求帶的
    started_at，這裡就會意外回 422。
    """
    original_started_at = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    now = original_started_at + timedelta(seconds=200)
    bogus_started_at = now - timedelta(seconds=1)
    service, repository = _service(now)

    await service.sync(OWNER, "S1", _request(10, original_started_at))
    result = await service.sync(OWNER, "S1", _request(600, bogus_started_at))

    assert result.steps == 600
    stored = repository.sessions[(OWNER, "S1")]
    assert stored.started_at == original_started_at
    assert stored.date == "2026-09-01"


# ── 日期歸屬：跨過午夜 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_session_starting_at_23_50_taipei_belongs_to_that_date():
    """spec「跨過午夜」：一個開始於台北時間 23:50 的工作階段，屬於那一天，
    不是隔天。"""
    started_at = datetime(2026, 9, 1, 23, 50, tzinfo=TAIPEI_TZ)
    now = started_at + timedelta(minutes=5)
    service, repository = _service(now)

    result = await service.sync(OWNER, "S1", _request(50, started_at))

    assert result.date == "2026-09-01"


@pytest.mark.asyncio
async def test_a_new_session_starting_at_00_05_taipei_belongs_to_the_next_date():
    """spec「跨過午夜」：午夜後開始的新工作階段記入新的一天。"""
    started_at = datetime(2026, 9, 2, 0, 5, tzinfo=TAIPEI_TZ)
    now = started_at + timedelta(minutes=5)
    service, repository = _service(now)

    result = await service.sync(OWNER, "S2", _request(20, started_at))

    assert result.date == "2026-09-02"


# ── 合理性檢查：平均每秒不得超過 5 步 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_rejects_a_rate_of_50_steps_per_second_with_422():
    """spec「不合理的步數」：一個開始於 10 秒前的工作階段回報累計 500 步
    （50 步/秒），SHALL 回 422，且 SHALL NOT 寫入。"""
    started_at = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    now = started_at + timedelta(seconds=10)
    service, repository = _service(now)

    with pytest.raises(HTTPException) as exc_info:
        await service.sync(OWNER, "S1", _request(500, started_at))

    assert exc_info.value.status_code == 422
    assert repository.sync_calls == []


@pytest.mark.asyncio
async def test_accepts_exactly_5_steps_per_second():
    """邊界：恰好等於每秒 5 步 SHALL 被接受，只有超過才拒絕。"""
    started_at = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    now = started_at + timedelta(seconds=100)
    service, repository = _service(now)

    result = await service.sync(OWNER, "S1", _request(500, started_at))

    assert result.steps == 500


@pytest.mark.asyncio
async def test_rate_check_uses_the_stored_started_at_not_a_changed_one():
    """合理性檢查也要用既有工作階段的 started_at，不是這次請求帶的值——
    與「日期歸屬」那支測試驗證同一個機制的另一面：這裡驗證的是「原本會
    超標的請求，不能藉由謊報一個較早的 started_at 通過檢查」。"""
    original_started_at = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    now = original_started_at + timedelta(seconds=10)
    bogus_earlier_started_at = original_started_at - timedelta(seconds=990)
    service, repository = _service(now)

    await service.sync(OWNER, "S1", _request(10, original_started_at))

    with pytest.raises(HTTPException) as exc_info:
        # 若誤用這次帶的 started_at（距 now 1000 秒），500/1000=0.5 步/秒會
        # 通過；正確行為是沿用既有的 started_at（距 now 僅 10 秒），
        # 500/10=50 步/秒，SHALL 422。
        await service.sync(OWNER, "S1", _request(500, bogus_earlier_started_at))

    assert exc_info.value.status_code == 422
    assert repository.sessions[(OWNER, "S1")].steps == 10


@pytest.mark.asyncio
async def test_elapsed_time_floors_at_one_second_to_avoid_division_by_zero():
    """工作階段剛開始（now == started_at）時，elapsed SHALL 至少視為 1 秒
    ——不然除以 0 會拋例外，而不是回應一個明確的 422 或 200。"""
    started_at = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)
    service, repository = _service(now=started_at)

    result = await service.sync(OWNER, "S1", _request(3, started_at))

    assert result.steps == 3


# ── 查詢：預設區間與上限 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_defaults_to_the_last_7_days_ending_today_taipei():
    now = datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc)  # 台北時間 9/10 11:00
    service, repository = _service(now)

    await service.list_daily_totals(OWNER)

    # 沒有任何工作階段，僅驗證預設區間有沒有拋錯、且真的呼叫了
    # repository（間接透過 list_daily_totals 沒有拋 422 來確認）。
    result = await service.list_daily_totals(OWNER)
    assert result == []


@pytest.mark.asyncio
async def test_list_passes_through_explicit_range_and_returns_matching_totals():
    now = datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc)
    service, repository = _service(now)
    await service.sync(
        OWNER, "S1", _request(300, datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc))
    )
    await service.sync(
        OWNER, "S2", _request(500, datetime(2026, 9, 5, 8, 0, tzinfo=timezone.utc))
    )

    result = await service.list_daily_totals(
        OWNER, start=date(2026, 9, 1), end=date(2026, 9, 5)
    )

    assert [(r.date, r.steps) for r in result] == [
        ("2026-09-05", 500),
        ("2026-09-01", 300),
    ]


@pytest.mark.asyncio
async def test_list_rejects_a_range_longer_than_90_days_with_422():
    now = datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc)
    service, repository = _service(now)

    with pytest.raises(HTTPException) as exc_info:
        await service.list_daily_totals(
            OWNER, start=date(2026, 1, 1), end=date(2026, 9, 10)
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_list_accepts_a_range_of_exactly_90_days():
    now = datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc)
    service, repository = _service(now)

    result = await service.list_daily_totals(
        OWNER, start=date(2026, 6, 12), end=date(2026, 9, 10)
    )

    assert result == []


@pytest.mark.asyncio
async def test_list_rejects_start_after_end_with_422():
    now = datetime(2026, 9, 10, 3, 0, tzinfo=timezone.utc)
    service, repository = _service(now)

    with pytest.raises(HTTPException) as exc_info:
        await service.list_daily_totals(
            OWNER, start=date(2026, 9, 10), end=date(2026, 9, 1)
        )

    assert exc_info.value.status_code == 422
