"""用藥風險通報的節流紀錄。

只有一個職責：讓「同一位使用者對同一個藥品在 TTL 內只通報一次」這件事成立，
而且是原子的。紀錄本身不是用藥資料，與 medications／medication_reminders
完全分離——背景偵測的結果未經任何人確認，不得流進用藥資料。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDBManager
from app.models.safety import RiskLevel, SafetyAlertRecord

logger = logging.getLogger(__name__)


class SafetyAlertRepository:
    """safety_alerts collection 的操作。"""

    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_safety_alerts_collection()
        # (user_id, drug_key) 唯一：通報權就是靠這個約束原子取得的，
        # 沒有它 try_claim 會退化成兩則訊息各推一次。
        await collection.create_index(
            [("user_id", 1), ("drug_key", 1)], unique=True
        )
        # expireAfterSeconds=0 表示以 expires_at 的時刻為準過期，由資料庫
        # 自行清除；節流視窗過了之後同一個藥才能再次通報。
        await collection.create_index("expires_at", expireAfterSeconds=0)

    @staticmethod
    async def try_claim(
        user_id: str,
        drug_key: str,
        risk_level: RiskLevel,
        ttl_hours: int,
        collection: Optional[Any] = None,
    ) -> bool:
        """嘗試取得這次的通報權，成功才可以推播。

        刻意不做「先查有沒有通報過、沒有才寫入」：同一位使用者連送兩則相似
        訊息時，兩邊都會在查詢當下判斷未通報而各自推播一次，家人因此收到重複
        通知。insert_one 撞上唯一索引就代表別人（或前一次）已經取得，直接回
        False，不需要額外的 CAS。
        """
        if collection is None:
            collection = MongoDBManager.get_safety_alerts_collection()

        notified_at = datetime.now(timezone.utc)
        record = SafetyAlertRecord(
            user_id=user_id,
            drug_key=drug_key,
            risk_level=risk_level,
            notified_at=notified_at,
            expires_at=notified_at + timedelta(hours=ttl_hours),
        )

        try:
            await collection.insert_one(record.model_dump())
        except DuplicateKeyError:
            # 節流期間內已通報過。這是預期路徑，不是錯誤。
            return False
        return True
