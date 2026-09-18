"""``HealthMeasurementService`` 的新增／查詢／刪除（health-measurements spec
「記錄血壓」「記錄血糖」「判定等級由後端計算」「查看紀錄」「刪除紀錄」）。

授權判定不在這裡——同 ``health_alert_threshold_service`` 的慣例，由
``app/routers/users/health.py`` 直接呼叫 ``FamilyAuthorizationService``。這裡
只驗證服務層自己該負責的部分：以本人**當下**的提醒範圍計算等級並儲存、
``recorded_by`` 保存實際記錄者、查詢預設區間與存在性、刪除的資料存取。

repository 以假的物件注入（tests/unit/services/health/test_health_level.py
之外，本檔案是全專案 DI 風格：不使用 ``unittest.mock.patch``）。
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pytest
from fastapi import HTTPException

from app.models.health import (
    CreateBloodGlucoseRequest,
    CreateBloodPressureRequest,
    HealthAlertThreshold,
    HealthMeasurement,
)
from app.services.health.health_measurement_service import HealthMeasurementService

ELDER = "U_ELDER"
GUARDIAN = "U_GUARDIAN"


class _FakeMeasurementRepository:
    """記錄呼叫、以遞增 id 模擬 Mongo 的假 repository。"""

    def __init__(self) -> None:
        self.added: List[HealthMeasurement] = []
        self.list_calls: List[tuple] = []
        self._store: Dict[str, HealthMeasurement] = {}
        self._next_id = 1

    async def add(self, measurement: HealthMeasurement) -> HealthMeasurement:
        stored = measurement.model_copy(update={"id": f"M{self._next_id}"})
        self._next_id += 1
        self.added.append(stored)
        self._store[stored.id] = stored
        return stored

    async def list_by_user(
        self,
        user_id: str,
        kind: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 200,
    ) -> List[HealthMeasurement]:
        self.list_calls.append((user_id, kind, start, end, limit))
        return [m for m in self._store.values() if m.user_id == user_id]

    async def get_by_id(self, measurement_id: str) -> Optional[HealthMeasurement]:
        return self._store.get(measurement_id)

    async def delete(self, measurement_id: str) -> bool:
        return self._store.pop(measurement_id, None) is not None


class _FakeThresholdRepository:
    def __init__(self, threshold: Optional[HealthAlertThreshold] = None) -> None:
        self.threshold = threshold
        self.get_calls: List[str] = []

    async def get(self, user_id: str) -> Optional[HealthAlertThreshold]:
        self.get_calls.append(user_id)
        return self.threshold


def _service(threshold: Optional[HealthAlertThreshold] = None):
    measurements = _FakeMeasurementRepository()
    thresholds = _FakeThresholdRepository(threshold)
    service = HealthMeasurementService(
        measurement_repository=measurements, threshold_repository=thresholds
    )
    return service, measurements, thresholds


# ── 新增：等級由本人當下的提醒範圍計算並儲存 ──────────────────────────


@pytest.mark.asyncio
async def test_create_blood_pressure_computes_level_from_owners_current_thresholds():
    threshold = HealthAlertThreshold(user_id=ELDER, systolic_high=140, updated_by=ELDER)
    service, measurements, thresholds = _service(threshold)

    result = await service.create(
        user_id=ELDER,
        recorded_by=ELDER,
        request=CreateBloodPressureRequest(systolic=152, diastolic=90),
    )

    assert result.level == "above_range"
    assert result.kind == "blood_pressure"
    assert result.systolic == 152
    assert result.diastolic == 90
    assert thresholds.get_calls == [ELDER]  # 讀的是「本人」的範圍，不是操作者的


@pytest.mark.asyncio
async def test_create_blood_glucose_computes_level_and_stores_meal_context():
    threshold = HealthAlertThreshold(
        user_id=ELDER, glucose_fasting_high=100, updated_by=ELDER
    )
    service, measurements, thresholds = _service(threshold)

    result = await service.create(
        user_id=ELDER,
        recorded_by=ELDER,
        request=CreateBloodGlucoseRequest(glucose_mg_dl=112, meal_context="fasting"),
    )

    assert result.level == "above_range"
    assert result.kind == "blood_glucose"
    assert result.glucose_mg_dl == 112
    assert result.meal_context == "fasting"


@pytest.mark.asyncio
async def test_create_with_no_threshold_document_is_no_threshold():
    service, measurements, thresholds = _service(threshold=None)

    result = await service.create(
        user_id=ELDER,
        recorded_by=ELDER,
        request=CreateBloodPressureRequest(systolic=128, diastolic=82),
    )

    assert result.level == "no_threshold"


@pytest.mark.asyncio
async def test_create_saves_recorded_by_distinct_from_owner_for_proxy_record():
    """主要照顧者代記（spec「主要照顧者代記」）：所有者是本人，記錄者是操作者。"""
    service, measurements, thresholds = _service()

    result = await service.create(
        user_id=ELDER,
        recorded_by=GUARDIAN,
        request=CreateBloodPressureRequest(systolic=128, diastolic=82),
    )

    assert result.user_id == ELDER
    assert result.recorded_by == GUARDIAN


@pytest.mark.asyncio
async def test_create_defaults_measured_at_to_now_when_omitted():
    service, measurements, thresholds = _service()
    before = datetime.now(timezone.utc)

    result = await service.create(
        user_id=ELDER,
        recorded_by=ELDER,
        request=CreateBloodPressureRequest(systolic=128, diastolic=82),
    )

    after = datetime.now(timezone.utc)
    measured_at = result.measured_at
    if measured_at.tzinfo is None:
        measured_at = measured_at.replace(tzinfo=timezone.utc)
    assert before <= measured_at <= after


@pytest.mark.asyncio
async def test_create_keeps_the_provided_measured_at_for_backfill():
    """補記過去的量測（spec「補記過去的量測」）：以該時間儲存，不是送出當下。"""
    service, measurements, thresholds = _service()
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)

    result = await service.create(
        user_id=ELDER,
        recorded_by=ELDER,
        request=CreateBloodPressureRequest(
            systolic=128, diastolic=82, measured_at=yesterday
        ),
    )

    measured_at = result.measured_at
    if measured_at.tzinfo is None:
        measured_at = measured_at.replace(tzinfo=timezone.utc)
    assert measured_at == yesterday


@pytest.mark.asyncio
async def test_stored_level_is_not_recomputed_after_thresholds_change():
    """提醒範圍變更後舊紀錄的等級不變（health-alerts spec「等級判定」；
    design.md 決策 3）：建立當下依 140 判定為 above_range；之後範圍改鬆，
    舊紀錄再次讀出時等級仍是 above_range，不會被重新計算成 within_range。
    """
    threshold = HealthAlertThreshold(user_id=ELDER, systolic_high=140, updated_by=ELDER)
    service, measurements, thresholds = _service(threshold)

    created = await service.create(
        user_id=ELDER,
        recorded_by=ELDER,
        request=CreateBloodPressureRequest(systolic=152, diastolic=90),
    )
    assert created.level == "above_range"

    # 範圍變更後——若讀取路徑重新呼叫 classify_measurement，這筆舊紀錄的
    # 152 相對新的上限 200 會變成 within_range；正確行為是完全不重算。
    thresholds.threshold = HealthAlertThreshold(
        user_id=ELDER, systolic_high=200, updated_by=ELDER
    )

    fetched = await service.get(created.id)
    assert fetched.level == "above_range"

    listed = await service.list(user_id=ELDER)
    assert listed[0].level == "above_range"


# ── 查詢：預設最近 30 天 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_defaults_to_last_30_days_ending_now_when_no_range_given():
    service, measurements, thresholds = _service()

    before = datetime.now(timezone.utc)
    await service.list(user_id=ELDER)
    after = datetime.now(timezone.utc)

    assert len(measurements.list_calls) == 1
    _, _, start, end, _ = measurements.list_calls[0]
    assert before - timedelta(seconds=5) <= end <= after + timedelta(seconds=5)
    assert start == end - timedelta(days=30)


@pytest.mark.asyncio
async def test_list_passes_through_explicit_kind_and_range():
    service, measurements, thresholds = _service()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)

    await service.list(user_id=ELDER, kind="blood_glucose", start=start, end=end)

    assert measurements.list_calls == [(ELDER, "blood_glucose", start, end, 200)]


# ── 刪除：不存在回 404 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_raises_404_when_the_record_does_not_exist():
    service, measurements, thresholds = _service()

    with pytest.raises(HTTPException) as exc_info:
        await service.get("does-not-exist")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_removes_the_record():
    service, measurements, thresholds = _service()
    created = await service.create(
        user_id=ELDER,
        recorded_by=ELDER,
        request=CreateBloodPressureRequest(systolic=128, diastolic=82),
    )

    await service.delete(created.id)

    assert await measurements.get_by_id(created.id) is None


# ── 接線：存檔之後呼叫超出範圍推播（health-alerts spec「推播失敗不影響
# 紀錄」；Task 7 dispatch notes「Wiring」）───────────────────────────────


class _FakeAlertService:
    def __init__(self, raise_error: bool = False) -> None:
        self.calls: List[tuple] = []
        self._raise = raise_error

    async def notify_out_of_range(self, measurement, thresholds):
        self.calls.append((measurement, thresholds))
        if self._raise:
            raise RuntimeError("推播服務掛了")


@pytest.mark.asyncio
async def test_create_calls_alert_service_with_the_saved_measurement_and_thresholds_used():
    threshold = HealthAlertThreshold(user_id=ELDER, systolic_high=140, updated_by=ELDER)
    measurements = _FakeMeasurementRepository()
    thresholds = _FakeThresholdRepository(threshold)
    alert_service = _FakeAlertService()
    service = HealthMeasurementService(
        measurement_repository=measurements,
        threshold_repository=thresholds,
        alert_service=alert_service,
    )

    created = await service.create(
        user_id=ELDER,
        recorded_by=ELDER,
        request=CreateBloodPressureRequest(systolic=152, diastolic=90),
    )

    assert len(alert_service.calls) == 1
    called_measurement, called_thresholds = alert_service.calls[0]
    assert called_measurement.id == created.id
    assert called_thresholds is threshold


@pytest.mark.asyncio
async def test_create_succeeds_even_when_the_alert_service_raises():
    """推播失敗 SHALL NOT 使新增紀錄的請求失敗（spec「推播失敗不影響紀錄」）。

    刻意注入一個會拋例外的替身，而不是完整的 ``HealthAlertService``（它自己
    也吞掉失敗，見該服務的 ``test_push_failure_is_swallowed``）：這裡驗證的
    是接線本身有沒有再包一層防禦，不假設「呼叫端一定接的是行為良好的實作」
    ——換一顆不同的注入物件不該讓 201 變成 500。
    """
    measurements = _FakeMeasurementRepository()
    thresholds = _FakeThresholdRepository()
    alert_service = _FakeAlertService(raise_error=True)
    service = HealthMeasurementService(
        measurement_repository=measurements,
        threshold_repository=thresholds,
        alert_service=alert_service,
    )

    result = await service.create(
        user_id=ELDER,
        recorded_by=ELDER,
        request=CreateBloodPressureRequest(systolic=152, diastolic=90),
    )

    assert result.id is not None
    assert len(measurements.added) == 1
    assert len(alert_service.calls) == 1


@pytest.mark.asyncio
async def test_create_without_alert_service_configured_still_works():
    service, measurements, thresholds = _service()

    result = await service.create(
        user_id=ELDER,
        recorded_by=ELDER,
        request=CreateBloodPressureRequest(systolic=128, diastolic=82),
    )

    assert result.id is not None
