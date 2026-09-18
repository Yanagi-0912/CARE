"""個人健康紀錄五份新 collection 的 `ensure_indexes` SHALL 在 lifespan 被呼叫。

比照 tests/unit/repositories/test_family_rbac_metrics_repository.py 的
test_startup_creates_indexes_for_every_new_collection：family-rbac 曾經漏掉
「寫了 ensure_indexes 但沒有任何地方呼叫」這一步，導致索引在正式環境永遠
不會存在（design.md「資料格式」段末段特別點名這個教訓）。以原始碼比對而非
啟動整個 app：lifespan 會連資料庫、建索引、載入院所名稱索引並組裝排程器，
在單元測試裡跑不動。
"""

import inspect

from app import main


def test_startup_creates_indexes_for_every_new_health_collection():
    source = inspect.getsource(main.lifespan)
    for repo in (
        "HealthMeasurementRepository",
        "HealthAlertThresholdRepository",
        "MenstrualRecordRepository",
        "StepSessionRepository",
        "HealthAlertClaimRepository",
    ):
        assert f"{repo}.ensure_indexes()" in source, f"{repo} 的索引不會被建立"
