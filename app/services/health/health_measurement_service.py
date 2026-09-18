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

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Union

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

logger = logging.getLogger(__name__)

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
        alert_service: Any = None,
    ) -> None:
        self._measurement_repository = measurement_repository
        self._threshold_repository = threshold_repository
        # Task 7：超出範圍推播（health-alerts spec）。型別刻意是 ``Any``——
        # 這裡不匯入 ``HealthAlertService``，避免為了型別標註而在兩個彼此
        # 沒有實際依賴關係的模組間造成 import 順序耦合；選填是因為既有呼叫端
        # （測試）尚未全部升級到會注入它。
        self._alert_service = alert_service

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
        # 超出範圍推播（health-alerts spec「超出範圍才推播」）。saved 已經
        # 有 _id／level／recorded_by，推播需要的欄位都齊了；thresholds 傳的
        # 是記錄當下實際用來判定等級的那一份，不讓推播服務重新讀取（記錄
        # 當下與推播當下之間範圍可能已經變更）。
        #
        # ``HealthAlertService.notify_out_of_range`` 自己已經吞掉內部的失敗
        # （同 emergency/otc alert service 的慣例），這裡仍在呼叫處再包一層
        # try/except——紀錄 SHALL 先寫入，推播才進行，且推播失敗 SHALL NOT
        # 使這支請求失敗（spec「推播失敗不影響紀錄」）；saved 已經回傳無望
        # 走到這裡失敗，這層是不假設「呼叫端一定接的是行為良好的實作」的
        # 防禦，不因為換一顆不同的注入物件就讓 201 變成 500。
        if self._alert_service is not None:
            try:
                await self._alert_service.notify_out_of_range(saved, thresholds)
            except Exception:  # noqa: BLE001
                logger.warning("超出範圍推播失敗，紀錄本身不受影響", exc_info=True)
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
