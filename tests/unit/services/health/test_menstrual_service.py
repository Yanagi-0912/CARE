"""``MenstrualRecordService`` 的新增／查詢／修正／刪除（menstrual-cycle-log
spec「僅女性使用者可建立」「記錄經期」「週期資訊由後端計算」）。

授權判定（跨使用者一律 403）不在這裡——同 ``health_measurement_service`` 的
慣例，由 ``app/routers/users/health.py`` 直接比對操作者與資料所有者的
識別碼，不經過家庭矩陣（見該檔案「經期」區塊的說明；constraints.md
「Menstrual」）。這裡只驗證服務層自己該負責的部分：建立時限性別為女性、
重疊檢查（含「開放中的紀錄視為佔滿 15 天」）、PATCH 的部分更新與整筆重驗、
查詢時後端計算週期長度與經期天數。

repository／user profile service 皆以假物件注入（全專案 DI 風格：不使用
``unittest.mock.patch``）。
"""

from datetime import date, timedelta
from typing import Dict, List, Optional

import pytest
from fastapi import HTTPException

from app.models.health import (
    CreateMenstrualRecordRequest,
    MenstrualRecord,
    UpdateMenstrualRecordRequest,
)
from app.services.health.menstrual_service import MenstrualRecordService

OWNER = "U_OWNER"


class _FakeMenstrualRepository:
    """記錄呼叫、以遞增 id 模擬 Mongo 的假 repository；重疊判定沿用真正的
    區間相交規則（同 ``MenstrualRecordRepository.find_overlapping``），
    這樣才能證明服務層真的把「本次的邊界」正確傳給了它，而不是只驗證
    服務層有沒有呼叫。"""

    def __init__(self, existing: Optional[List[MenstrualRecord]] = None) -> None:
        self._store: Dict[str, MenstrualRecord] = {
            r.id: r for r in (existing or []) if r.id
        }
        # 從既有紀錄之後接續編號，避免與預先塞入的 id（測試常用 "R1"、"R2"）
        # 相撞而在 dict 裡覆蓋掉既有紀錄。
        self._next_id = len(self._store) + 1
        self.add_calls: List[MenstrualRecord] = []
        self.update_calls: List[tuple] = []
        self.delete_calls: List[str] = []

    async def add(self, record: MenstrualRecord) -> MenstrualRecord:
        stored = record.model_copy(update={"id": f"R{self._next_id}"})
        self._next_id += 1
        self._store[stored.id] = stored
        self.add_calls.append(stored)
        return stored

    async def list_by_user(self, user_id: str) -> List[MenstrualRecord]:
        records = [r for r in self._store.values() if r.user_id == user_id]
        return sorted(records, key=lambda r: r.start_date, reverse=True)

    async def get_by_id(self, record_id: str) -> Optional[MenstrualRecord]:
        return self._store.get(record_id)

    async def update(self, record_id: str, update_data: dict) -> Optional[MenstrualRecord]:
        self.update_calls.append((record_id, dict(update_data)))
        existing = self._store.get(record_id)
        if existing is None:
            return None
        updated = existing.model_copy(update=update_data)
        self._store[record_id] = updated
        return updated

    async def delete(self, record_id: str) -> bool:
        self.delete_calls.append(record_id)
        return self._store.pop(record_id, None) is not None

    async def find_overlapping(
        self,
        user_id: str,
        start_date: str,
        end_date: Optional[str] = None,
        exclude_id: Optional[str] = None,
    ) -> List[MenstrualRecord]:
        MAX_SPAN = 15
        new_start = date.fromisoformat(start_date)
        new_end = (
            date.fromisoformat(end_date)
            if end_date
            else new_start + timedelta(days=MAX_SPAN)
        )

        def _effective_end(record: MenstrualRecord) -> date:
            if record.end_date:
                return date.fromisoformat(record.end_date)
            return date.fromisoformat(record.start_date) + timedelta(days=MAX_SPAN)

        overlapping = []
        for record in self._store.values():
            if record.user_id != user_id or record.id == exclude_id:
                continue
            existing_start = date.fromisoformat(record.start_date)
            if existing_start <= new_end and new_start <= _effective_end(record):
                overlapping.append(record)
        return overlapping


class _FakeUserProfileService:
    def __init__(self, profiles: Optional[Dict[str, dict]] = None) -> None:
        self._profiles = profiles or {}

    async def get_user_profile(self, line_id: str):
        return self._profiles.get(line_id)


def _service(
    existing: Optional[List[MenstrualRecord]] = None,
    gender: Optional[str] = "female",
):
    repository = _FakeMenstrualRepository(existing)
    profiles = {OWNER: {"gender": gender}} if gender is not None else {}
    profile_service = _FakeUserProfileService(profiles)
    service = MenstrualRecordService(
        repository=repository, user_profile_service=profile_service
    )
    return service, repository


# ── 建立：僅女性使用者可建立 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_succeeds_for_female_gender():
    service, repository = _service(gender="female")

    result = await service.create(
        OWNER, CreateMenstrualRecordRequest(start_date="2026-09-01")
    )

    assert result.user_id == OWNER
    assert result.end_date is None
    assert len(repository.add_calls) == 1


@pytest.mark.asyncio
async def test_create_rejects_male_gender_with_403_explaining_gender_setup():
    service, _ = _service(gender="male")

    with pytest.raises(HTTPException) as exc_info:
        await service.create(OWNER, CreateMenstrualRecordRequest(start_date="2026-09-01"))

    assert exc_info.value.status_code == 403
    assert "性別" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_rejects_unset_gender_with_403():
    service, _ = _service(gender="unknown")

    with pytest.raises(HTTPException) as exc_info:
        await service.create(OWNER, CreateMenstrualRecordRequest(start_date="2026-09-01"))

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_create_rejects_missing_profile_with_403():
    """從未填過個人健康檔案：沒有文件，gender 視為未設定。"""
    service, _ = _service(gender=None)

    with pytest.raises(HTTPException) as exc_info:
        await service.create(OWNER, CreateMenstrualRecordRequest(start_date="2026-09-01"))

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_existing_records_stay_manageable_after_gender_change():
    """變更性別後仍可管理舊紀錄（spec「變更性別後仍可管理舊紀錄」）：GET／
    PATCH／DELETE 不檢查性別，只有 CREATE 檢查。"""
    existing = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-08-01")
    service, repository = _service(existing=[existing], gender="male")

    listed = await service.list(OWNER)
    assert [r.id for r in listed] == ["R1"]

    updated = await service.update(
        "R1", UpdateMenstrualRecordRequest(note="補充說明")
    )
    assert updated.note == "補充說明"

    await service.delete("R1")
    assert repository.delete_calls == ["R1"]


# ── 建立：重疊回 409 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_rejects_overlap_with_409_and_does_not_write():
    existing = MenstrualRecord(
        id="R1", user_id=OWNER, start_date="2026-09-01", end_date="2026-09-05"
    )
    service, repository = _service(existing=[existing])

    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            OWNER, CreateMenstrualRecordRequest(start_date="2026-09-03")
        )

    assert exc_info.value.status_code == 409
    assert repository.add_calls == []


@pytest.mark.asyncio
async def test_create_rejects_start_date_inside_an_open_ended_existing_record():
    """開放中的紀錄視為佔滿 15 天：新開始日期落在這個窗口內即算重疊
    （dispatch notes「Overlap → 409」）。"""
    existing = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-09-01")
    service, repository = _service(existing=[existing])

    with pytest.raises(HTTPException) as exc_info:
        await service.create(
            OWNER, CreateMenstrualRecordRequest(start_date="2026-09-10")
        )

    assert exc_info.value.status_code == 409
    assert repository.add_calls == []


@pytest.mark.asyncio
async def test_create_allows_a_new_period_after_a_stale_open_record():
    """一筆很久以前忘記填結束日期的紀錄，不擋住這個月的新紀錄。"""
    existing = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-01-01")
    service, repository = _service(existing=[existing])

    result = await service.create(
        OWNER, CreateMenstrualRecordRequest(start_date="2026-09-01")
    )

    assert result.user_id == OWNER
    assert len(repository.add_calls) == 1


@pytest.mark.asyncio
async def test_create_allows_a_non_overlapping_period():
    existing = MenstrualRecord(
        id="R1", user_id=OWNER, start_date="2026-08-01", end_date="2026-08-05"
    )
    service, repository = _service(existing=[existing])

    result = await service.create(
        OWNER, CreateMenstrualRecordRequest(start_date="2026-09-01")
    )

    assert result.user_id == OWNER
    assert len(repository.add_calls) == 1


# ── 查詢：週期資訊由後端計算 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_computes_cycle_length_from_the_previous_older_record():
    older = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-08-01")
    newer = MenstrualRecord(id="R2", user_id=OWNER, start_date="2026-08-30")
    service, _ = _service(existing=[older, newer])

    listed = await service.list(OWNER)

    by_id = {r.id: r for r in listed}
    assert by_id["R2"].cycle_length_days == 29
    assert by_id["R1"].cycle_length_days is None  # 最舊一筆沒有前一筆


@pytest.mark.asyncio
async def test_list_computes_period_length_as_inclusive_span():
    record = MenstrualRecord(
        id="R1", user_id=OWNER, start_date="2026-09-01", end_date="2026-09-05"
    )
    service, _ = _service(existing=[record])

    listed = await service.list(OWNER)

    assert listed[0].period_length_days == 5  # 1 號到 5 號含頭尾共 5 天


@pytest.mark.asyncio
async def test_list_period_length_is_none_when_still_ongoing():
    record = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-09-01")
    service, _ = _service(existing=[record])

    listed = await service.list(OWNER)

    assert listed[0].period_length_days is None


@pytest.mark.asyncio
async def test_list_is_sorted_newest_first():
    older = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-08-01")
    newer = MenstrualRecord(id="R2", user_id=OWNER, start_date="2026-08-30")
    service, _ = _service(existing=[older, newer])

    listed = await service.list(OWNER)

    assert [r.id for r in listed] == ["R2", "R1"]


@pytest.mark.asyncio
async def test_create_response_carries_the_same_computed_fields():
    older = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-08-01")
    service, _ = _service(existing=[older])

    created = await service.create(
        OWNER, CreateMenstrualRecordRequest(start_date="2026-08-30")
    )

    assert created.cycle_length_days == 29


# ── PATCH：部分更新、重疊重驗、重新打開 ──────────────────────────────────


@pytest.mark.asyncio
async def test_update_fills_in_end_date_for_an_ongoing_period():
    existing = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-09-01")
    service, repository = _service(existing=[existing])

    updated = await service.update(
        "R1", UpdateMenstrualRecordRequest(end_date="2026-09-05")
    )

    assert updated.end_date == "2026-09-05"
    assert updated.period_length_days == 5


@pytest.mark.asyncio
async def test_update_only_overwrites_fields_actually_sent():
    existing = MenstrualRecord(
        id="R1",
        user_id=OWNER,
        start_date="2026-09-01",
        end_date="2026-09-05",
        flow="medium",
        note="原本的備註",
    )
    service, repository = _service(existing=[existing])

    updated = await service.update("R1", UpdateMenstrualRecordRequest(flow="heavy"))

    assert updated.flow == "heavy"
    assert updated.start_date == "2026-09-01"
    assert updated.end_date == "2026-09-05"
    assert updated.note == "原本的備註"


@pytest.mark.asyncio
async def test_update_reopens_the_record_when_end_date_explicitly_set_to_null():
    existing = MenstrualRecord(
        id="R1", user_id=OWNER, start_date="2026-09-01", end_date="2026-09-05"
    )
    service, repository = _service(existing=[existing])

    updated = await service.update(
        "R1", UpdateMenstrualRecordRequest(end_date=None)
    )

    assert updated.end_date is None
    assert updated.period_length_days is None


@pytest.mark.asyncio
async def test_update_revalidates_the_merged_record_with_422():
    """更新後的結果整筆不合法（間隔超過 15 天）：422，不寫入。"""
    existing = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-09-01")
    service, repository = _service(existing=[existing])

    with pytest.raises(HTTPException) as exc_info:
        await service.update(
            "R1", UpdateMenstrualRecordRequest(end_date="2026-09-20")
        )

    assert exc_info.value.status_code == 422
    assert repository.update_calls == []


@pytest.mark.asyncio
async def test_update_rechecks_overlap_excluding_itself():
    """自己的日期沒變時，重新檢查重疊 SHALL NOT 把自己算進去。"""
    existing = MenstrualRecord(
        id="R1", user_id=OWNER, start_date="2026-09-01", end_date="2026-09-05"
    )
    service, repository = _service(existing=[existing])

    updated = await service.update("R1", UpdateMenstrualRecordRequest(note="ok"))

    assert updated.note == "ok"


@pytest.mark.asyncio
async def test_update_rejects_overlap_with_another_record_with_409():
    a = MenstrualRecord(
        id="R1", user_id=OWNER, start_date="2026-09-01", end_date="2026-09-05"
    )
    b = MenstrualRecord(id="R2", user_id=OWNER, start_date="2026-09-20")
    service, repository = _service(existing=[a, b])

    with pytest.raises(HTTPException) as exc_info:
        await service.update(
            "R2", UpdateMenstrualRecordRequest(start_date="2026-09-03")
        )

    assert exc_info.value.status_code == 409
    assert repository.update_calls == []


@pytest.mark.asyncio
async def test_update_raises_404_when_missing():
    service, _ = _service()

    with pytest.raises(HTTPException) as exc_info:
        await service.update("MISSING", UpdateMenstrualRecordRequest(note="x"))

    assert exc_info.value.status_code == 404


# ── 讀取單筆：不存在回 404 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_raises_404_when_missing():
    service, _ = _service()

    with pytest.raises(HTTPException) as exc_info:
        await service.get("MISSING")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_returns_the_record_when_found():
    existing = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-09-01")
    service, _ = _service(existing=[existing])

    result = await service.get("R1")

    assert result.id == "R1"


# ── 刪除 ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_removes_the_record():
    existing = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-09-01")
    service, repository = _service(existing=[existing])

    await service.delete("R1")

    assert await repository.get_by_id("R1") is None


# ── 接線：存檔之後呼叫經期異常推播（health-alerts spec「經期異常只通知
# 本人」「推播失敗不影響紀錄」；Task 7 dispatch notes「Wiring」）─────────


class _FakeAlertService:
    def __init__(self, raise_error: bool = False) -> None:
        self.calls: List[tuple] = []
        self._raise = raise_error

    async def notify_menstrual_anomaly(self, user_id: str, record_id: str) -> None:
        self.calls.append((user_id, record_id))
        if self._raise:
            raise RuntimeError("推播服務掛了")


def _service_with_alert(
    existing: Optional[List[MenstrualRecord]] = None, alert_service=None
):
    repository = _FakeMenstrualRepository(existing)
    profile_service = _FakeUserProfileService({OWNER: {"gender": "female"}})
    alert_service = alert_service if alert_service is not None else _FakeAlertService()
    service = MenstrualRecordService(
        repository=repository,
        user_profile_service=profile_service,
        alert_service=alert_service,
    )
    return service, repository, alert_service


@pytest.mark.asyncio
async def test_create_notifies_anomaly_when_cycle_is_too_long():
    """週期 > 38 天（spec「週期過長」）。"""
    older = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-06-01")
    service, repository, alert_service = _service_with_alert(existing=[older])

    created = await service.create(
        OWNER, CreateMenstrualRecordRequest(start_date="2026-07-30")  # 59 天
    )

    assert alert_service.calls == [(OWNER, created.id)]


@pytest.mark.asyncio
async def test_create_notifies_anomaly_when_cycle_is_too_short():
    """週期 < 24 天。前一筆帶結束日期，讓「開放中的紀錄視為佔滿 15 天」不會
    誤把這個間隔擋成重疊（見 repository 對 ``find_overlapping`` 的說明）。
    """
    older = MenstrualRecord(
        id="R1", user_id=OWNER, start_date="2026-08-01", end_date="2026-08-04"
    )
    service, repository, alert_service = _service_with_alert(existing=[older])

    created = await service.create(
        OWNER, CreateMenstrualRecordRequest(start_date="2026-08-15")  # 14 天
    )

    assert alert_service.calls == [(OWNER, created.id)]


@pytest.mark.asyncio
async def test_create_does_not_notify_when_cycle_is_normal():
    """週期正常（spec「週期正常」：間隔 29 天 SHALL NOT 推播）。"""
    older = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-08-01")
    service, repository, alert_service = _service_with_alert(existing=[older])

    await service.create(OWNER, CreateMenstrualRecordRequest(start_date="2026-08-30"))

    assert alert_service.calls == []


@pytest.mark.asyncio
async def test_create_notifies_anomaly_when_end_date_makes_the_period_too_long():
    """建立時就帶結束日期、經期 > 8 天。"""
    service, repository, alert_service = _service_with_alert()

    created = await service.create(
        OWNER,
        CreateMenstrualRecordRequest(start_date="2026-09-01", end_date="2026-09-10"),
    )

    assert alert_service.calls == [(OWNER, created.id)]


@pytest.mark.asyncio
async def test_create_without_previous_record_or_end_date_does_not_notify():
    """第一筆紀錄，沒有前一筆可比、也還沒有結束日期：兩半都還沒有值可判斷，
    SHALL NOT 視為異常。"""
    service, repository, alert_service = _service_with_alert()

    await service.create(OWNER, CreateMenstrualRecordRequest(start_date="2026-09-01"))

    assert alert_service.calls == []


@pytest.mark.asyncio
async def test_update_notifies_anomaly_when_end_date_makes_the_period_too_long():
    """PATCH 補上結束日期、經期共 10 天（spec「經期過長」）。"""
    existing = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-09-01")
    service, repository, alert_service = _service_with_alert(existing=[existing])

    await service.update("R1", UpdateMenstrualRecordRequest(end_date="2026-09-10"))

    assert alert_service.calls == [(OWNER, "R1")]


@pytest.mark.asyncio
async def test_update_does_not_notify_when_end_date_is_not_touched():
    """只改 note，沒有帶 end_date：不該多一次判定／claim 嘗試。"""
    existing = MenstrualRecord(
        id="R1", user_id=OWNER, start_date="2026-09-01", end_date="2026-09-05"
    )
    service, repository, alert_service = _service_with_alert(existing=[existing])

    await service.update("R1", UpdateMenstrualRecordRequest(note="備註"))

    assert alert_service.calls == []


@pytest.mark.asyncio
async def test_update_does_not_notify_when_the_period_is_normal():
    existing = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-09-01")
    service, repository, alert_service = _service_with_alert(existing=[existing])

    await service.update("R1", UpdateMenstrualRecordRequest(end_date="2026-09-03"))

    assert alert_service.calls == []


@pytest.mark.asyncio
async def test_create_succeeds_even_when_the_alert_service_raises():
    """推播失敗 SHALL NOT 使新增紀錄的請求失敗（spec「推播失敗不影響紀錄」）。
    同 ``test_health_measurement_service.py`` 的理由：接線本身要有防禦，
    不假設注入的一定是行為良好的實作。"""
    older = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-06-01")
    service, repository, alert_service = _service_with_alert(
        existing=[older], alert_service=_FakeAlertService(raise_error=True)
    )

    created = await service.create(
        OWNER, CreateMenstrualRecordRequest(start_date="2026-07-30")
    )

    assert created.id is not None
    assert len(repository.add_calls) == 1
    assert len(alert_service.calls) == 1


@pytest.mark.asyncio
async def test_update_succeeds_even_when_the_alert_service_raises():
    existing = MenstrualRecord(id="R1", user_id=OWNER, start_date="2026-09-01")
    service, repository, alert_service = _service_with_alert(
        existing=[existing], alert_service=_FakeAlertService(raise_error=True)
    )

    updated = await service.update("R1", UpdateMenstrualRecordRequest(end_date="2026-09-10"))

    assert updated.end_date == "2026-09-10"
    assert len(alert_service.calls) == 1


@pytest.mark.asyncio
async def test_create_without_alert_service_configured_still_works():
    service, repository = _service()

    result = await service.create(
        OWNER, CreateMenstrualRecordRequest(start_date="2026-09-01")
    )

    assert result.id is not None


@pytest.mark.asyncio
async def test_maybe_notify_anomaly_swallows_unparseable_stored_dates():
    """程式碼審查發現：``_maybe_notify_anomaly`` 原本只把推播那一次呼叫包進
    try/except，上面的 ``date.fromisoformat``／``detect_menstrual_anomaly``
    仍在保護傘外——存進資料庫的日期字串一旦壞掉（不論任何原因：舊資料、
    直接寫入資料庫等），``date.fromisoformat`` 會拋出 ``ValueError``，讓一支
    原本該成功的請求變成 500，正是 spec「推播失敗不影響紀錄」要擋的事。

    ``create()``／``update()`` 兩條公開路徑目前都會在更早之前（
    ``CreateMenstrualRecordRequest`` 的欄位驗證、或 ``_compute_fields_for_list``
    對整份清單的日期解析）先擋下格式不合法的日期字串，因此無法在不繞過既有
    驗證的前提下，端對端地把一筆「日期解析失敗」的紀錄送進
    ``_maybe_notify_anomaly``。這裡直接呼叫該方法本身——它是這次要修的那個
    邊界，這樣測的正是「就算收到解析不了的日期，這個方法也不對外拋出例外、
    也不會推播」，不多也不少。
    """
    service, _ = _service()
    broken = MenstrualRecord(id="R1", user_id=OWNER, start_date="not-a-date")

    # 不該拋出例外——即使上面這行本身已經違反其他地方的驗證慣例。
    await service._maybe_notify_anomaly(OWNER, broken)


@pytest.mark.asyncio
async def test_maybe_notify_anomaly_swallows_unparseable_end_date():
    class _FakeAlertService:
        def __init__(self) -> None:
            self.calls: List[tuple] = []

        async def notify_menstrual_anomaly(self, user_id: str, record_id: str) -> None:
            self.calls.append((user_id, record_id))

    repository = _FakeMenstrualRepository()
    alert_service = _FakeAlertService()
    service = MenstrualRecordService(
        repository=repository,
        user_profile_service=_FakeUserProfileService({OWNER: {"gender": "female"}}),
        alert_service=alert_service,
    )
    broken = MenstrualRecord(
        id="R1", user_id=OWNER, start_date="2026-09-01", end_date="not-a-date"
    )

    await service._maybe_notify_anomaly(OWNER, broken)

    assert alert_service.calls == []
