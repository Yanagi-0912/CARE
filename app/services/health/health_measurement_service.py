"""血壓／血糖量測 (health_measurements) 的服務層。

授權判定不在這裡——同 ``health_alert_threshold_service`` 的慣例：由
``app/routers/users/health.py`` 直接呼叫 ``FamilyAuthorizationService``。這裡
只負責三件事：

- 新增時以**本人（資料所有者）當下**的提醒範圍呼叫 ``classify_measurement``
  （Task 3）算出等級並與 ``recorded_by`` 一起存進紀錄——等級只在建立當下
  計算、儲存後不再重算（design.md 決策 3；health-alerts spec「等級判定」）。
- 查詢時補上「未指定區間時最近 30 天」的預設值，其餘（排序、200 筆上限）
  已經是 ``HealthMeasurementRepository.list_by_user`` 的責任，這裡不重複。
- 刪除前先確認紀錄存在（不存在回 404），讓呼叫端（router）能在授權判定時
  取得紀錄的所有者是誰。
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union

from fastapi import HTTPException

from app.models.health import (
    CreateBloodGlucoseRequest,
    CreateBloodPressureRequest,
    HealthMeasurement,
    MeasurementKind,
)
from app.repositories.health_alert_threshold_repository import (
    HealthAlertThresholdRepository,
)
from app.repositories.health_measurement_repository import HealthMeasurementRepository
from app.services.health.health_level import classify_measurement

# 查詢未指定區間時，回傳最近 30 天（constraints.md「狀態碼」段；
# health-measurements spec「查看紀錄」）。
DEFAULT_QUERY_RANGE_DAYS = 30

CreateMeasurementRequest = Union[CreateBloodPressureRequest, CreateBloodGlucoseRequest]


class HealthMeasurementService:
    """``health_measurements`` 的讀寫。repository 以類別（或相同介面的假物件）
    注入，方便測試換成假的（同 ``HealthAlertThresholdService`` 的慣例）。"""

    def __init__(
        self,
        measurement_repository: type[
            HealthMeasurementRepository
        ] = HealthMeasurementRepository,
        threshold_repository: type[
            HealthAlertThresholdRepository
        ] = HealthAlertThresholdRepository,
    ) -> None:
        self._measurement_repository = measurement_repository
        self._threshold_repository = threshold_repository

    async def create(
        self,
        user_id: str,
        recorded_by: str,
        request: CreateMeasurementRequest,
    ) -> HealthMeasurement:
        """新增一筆血壓或血糖紀錄。

        ``user_id`` 是資料本人（代記時是被記錄的長輩，不是操作者）；等級
        依**本人**當下的提醒範圍計算——代記時看的是本人的範圍，不是操作者
        的範圍，兩者概念上不同（提醒範圍本來就沒有「操作者的範圍」這回事，
        這裡刻意寫清楚是因為呼叫端很容易誤傳操作者 id）。
        """
        is_blood_pressure = isinstance(request, CreateBloodPressureRequest)
        kind: MeasurementKind = "blood_pressure" if is_blood_pressure else "blood_glucose"
        measured_at = request.measured_at or datetime.now(timezone.utc)

        thresholds = await self._threshold_repository.get(user_id)
        level = classify_measurement(request, thresholds)

        if is_blood_pressure:
            assert isinstance(request, CreateBloodPressureRequest)
            measurement = HealthMeasurement(
                user_id=user_id,
                kind=kind,
                measured_at=measured_at,
                recorded_by=recorded_by,
                systolic=request.systolic,
                diastolic=request.diastolic,
                pulse=request.pulse,
                level=level,
            )
        else:
            assert isinstance(request, CreateBloodGlucoseRequest)
            measurement = HealthMeasurement(
                user_id=user_id,
                kind=kind,
                measured_at=measured_at,
                recorded_by=recorded_by,
                glucose_mg_dl=request.glucose_mg_dl,
                meal_context=request.meal_context,
                level=level,
            )

        saved = await self._measurement_repository.add(measurement)
        # ── 存檔之後 ──────────────────────────────────────────────────
        # Task 7 會在這裡接上超出範圍推播（health-alerts spec「超出範圍才
        # 推播」）：saved 已經有 _id／level／recorded_by，推播需要的欄位都
        # 齊了。推播失敗 SHALL NOT 影響這支請求的結果，因此掛在存檔「之後」。
        return saved

    async def list(
        self,
        user_id: str,
        kind: Optional[MeasurementKind] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[HealthMeasurement]:
        """查詢紀錄。未指定 ``start``／``end`` 時預設最近 30 天、以查詢當下
        為結束時間；只指定其中一端時，另一端維持開放區間，交給 repository
        的 ``$gte``／``$lte`` 各自處理。排序與 200 筆上限已在 repository。
        """
        if start is None and end is None:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=DEFAULT_QUERY_RANGE_DAYS)

        return await self._measurement_repository.list_by_user(
            user_id=user_id, kind=kind, start=start, end=end
        )

    async def get(self, measurement_id: str) -> HealthMeasurement:
        """取得單筆紀錄，供呼叫端（router）在授權判定之前得知其所有者是誰。
        不存在時 SHALL 回 404（spec「刪除紀錄」）。
        """
        measurement = await self._measurement_repository.get_by_id(measurement_id)
        if measurement is None:
            raise HTTPException(status_code=404, detail="找不到該筆紀錄")
        return measurement

    async def delete(self, measurement_id: str) -> None:
        """刪除一筆紀錄。存在性已由呼叫端先呼叫 ``get`` 確認過，這裡不重複
        判斷；刪除紀錄 SHALL NOT 撤回已經送出的通知（spec「刪除紀錄」）——
        本方法不觸碰任何通知相關的資料。
        """
        await self._measurement_repository.delete(measurement_id)
