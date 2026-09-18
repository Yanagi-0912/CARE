"""健康提醒推播的節流紀錄 (health_alert_claims)。

沿用 ``app/repositories/safety_alert_repository.py`` 的模式：通報權由
``(user_id, alert_key)`` 的唯一索引原子取得，``expires_at`` 交給 TTL 索引
自動清除，不依賴應用端排程；同一位本人、同一類別在節流視窗內只會有一邊
``insert_one`` 成功。

``alert_key`` 的形狀由呼叫端決定（health-alerts spec「重複推播的節流」）：
血壓血糖是 ``bp_high``／``bp_low``／``glucose_high``／``glucose_low`` 四類，
30 分鐘節流；經期異常是 ``menstrual:<record_id>``，每筆紀錄最多通知一次。
兩者的 ``ttl_minutes`` 由呼叫端各自決定，這裡不寫死。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDBManager
from app.models.health import HealthAlertClaim

logger = logging.getLogger(__name__)


class HealthAlertClaimRepository:
    """``health_alert_claims`` collection 的操作。"""

    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_health_alert_claims_collection()
        # (user_id, alert_key) 唯一：節流權就是靠這個約束原子取得的。
        await collection.create_index(
            [("user_id", 1), ("alert_key", 1)], unique=True
        )
        # expireAfterSeconds=0：以 expires_at 的時刻為準過期，節流視窗過了
        # 之後同一類別才能再次取得通報權。
        await collection.create_index("expires_at", expireAfterSeconds=0)

    @staticmethod
    async def try_claim(
        user_id: str,
        alert_key: str,
        ttl_minutes: int,
        collection: Optional[Any] = None,
    ) -> bool:
        """嘗試取得這次的推播權，成功才可以推播。

        刻意不做「先查有沒有推播過、沒有才寫入」：insert_one 撞上唯一索引
        就代表別人（或前一次）已經取得，直接回 False，不需要額外的 CAS
        （同 SafetyAlertRepository.try_claim 的理由）。
        """
        if collection is None:
            collection = MongoDBManager.get_health_alert_claims_collection()

        claimed_at = datetime.now(timezone.utc)
        record = HealthAlertClaim(
            user_id=user_id,
            alert_key=alert_key,
            claimed_at=claimed_at,
            expires_at=claimed_at + timedelta(minutes=ttl_minutes),
        )

        try:
            await collection.insert_one(record.model_dump())
        except DuplicateKeyError:
            # 節流期間內已推播過。這是預期路徑，不是錯誤。
            return False
        return True
