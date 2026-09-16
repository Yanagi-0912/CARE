"""提醒範圍（``health_alert_thresholds``）的服務層。

授權判定不在這裡——由 ``app/routers/users/health.py`` 直接呼叫
``FamilyAuthorizationService.authorize``（同 medications／upsert_users 的
慣例：授權是端點的責任，服務層只管資料）。這裡只回答兩件事：讀出來的形狀
（沒有文件時該長什麼樣子）、寫進去時該補上什麼欄位。
"""

from typing import Any, Dict, Optional

from app.models.health import HealthAlertThreshold, UpdateHealthAlertThresholdRequest
from app.repositories.health_alert_threshold_repository import (
    HealthAlertThresholdRepository,
)

# ``HealthAlertThreshold`` 的 updated_by／updated_at 是必填欄位（一份文件
# 一定有最後修改者），沒有文件時無法直接用這個模型表示「全部未設定」——
# 因此 GET 沒有文件時，回應改由這裡組出一份形狀相同、七項範圍與
# updated_by／updated_at 皆為 null 的 dict（health-alerts spec「使用者
# 自訂提醒範圍」；design.md「資料格式」：沒有文件等同全部未設定）。
_EMPTY_THRESHOLD_FIELDS = (
    "systolic_high",
    "systolic_low",
    "diastolic_high",
    "diastolic_low",
    "glucose_fasting_high",
    "glucose_nonfasting_high",
    "glucose_low",
)


class HealthAlertThresholdService:
    """``health_alert_thresholds`` 的讀寫。repository 以類別注入，方便測試
    換成假的（同專案其餘 repository 皆為 staticmethod 群組的慣例）。"""

    def __init__(
        self, repository: type[HealthAlertThresholdRepository] = HealthAlertThresholdRepository
    ) -> None:
        self._repository = repository

    async def get_view(self, user_id: str) -> Dict[str, Any]:
        """回傳查看提醒範圍的回應形狀。沒有文件時 SHALL NOT 回 404、也
        SHALL NOT 頂替任何預設值（health-alerts spec「使用者自訂提醒
        範圍」：系統不內建任何預設的提醒範圍）——一律 200，七項範圍與
        updated_by／updated_at 皆為 null。
        """
        threshold = await self._repository.get(user_id)
        if threshold is None:
            empty: Dict[str, Any] = {"user_id": user_id}
            empty.update({field: None for field in _EMPTY_THRESHOLD_FIELDS})
            empty["updated_by"] = None
            empty["updated_at"] = None
            return empty
        return threshold.model_dump()

    async def update(
        self,
        target_user_id: str,
        request: UpdateHealthAlertThresholdRequest,
        updated_by: str,
    ) -> HealthAlertThreshold:
        """整份覆寫（health-alerts spec「清除一項」：沒帶到或為 null 的欄位
        視為清除）。``request`` 已在 Pydantic 邊界驗證過範圍與上下限關係，
        這裡只負責補上 ``user_id``／``updated_by``，不重新驗證。
        """
        threshold = HealthAlertThreshold(
            user_id=target_user_id,
            updated_by=updated_by,
            **request.model_dump(),
        )
        return await self._repository.upsert(threshold)
