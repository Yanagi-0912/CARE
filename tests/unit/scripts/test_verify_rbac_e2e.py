"""``scripts/verify_rbac_e2e.py`` 純函式的守門測試。

這支腳本本體要打真實的本機後端，不適合在單元測試裡跑（見
``.superpowers/sdd/tasks/task-12-dispatch-notes.md`` 的「live PASS run 交由
controller 或使用者」）。這裡只測試它拆出來、不碰網路的純函式部分：
GUARDIAN id 的換算，與 (f)(i) 兩個檢查送出去的 body 本身是否合法。
"""

import importlib.util
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "verify_rbac_e2e.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_rbac_e2e", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = _load_module()


def test_derive_guardian_id_from_owner_id():
    """seed_rbac_e2e.py 的帳號命名規則是 PREFIX + 角色名——OWNER_ID 換算
    GUARDIAN_ID 只要把尾碼換掉。"""
    assert verify.derive_guardian_id("U_E2E_OWNER") == "U_E2E_GUARDIAN"


def test_derive_guardian_id_rejects_unexpected_shape():
    """換算不出來（id 不是以 OWNER 結尾）時 SHALL 早點爆，不要讓後面的 403
    檢查因為打錯 URL 而誤判成授權結果。"""
    with pytest.raises(AssertionError):
        verify.derive_guardian_id("SOMETHING_ELSE")


def test_threshold_body_satisfies_the_request_model():
    """(f) 送出的 body 要能通過 UpdateHealthAlertThresholdRequest 的驗證，
    否則收到的是驗證失敗的 422，不是授權判定的 403，會被誤讀成通過。"""
    from app.models.health import UpdateHealthAlertThresholdRequest

    body = json.loads(verify.build_threshold_body())
    UpdateHealthAlertThresholdRequest(**body)


def test_step_body_satisfies_the_request_model():
    """(i) 送出的 body 要能通過 StepSessionSyncRequest 的驗證，同上一個測試
    的理由。"""
    from app.models.health import StepSessionSyncRequest

    body = json.loads(verify.build_step_body())
    StepSessionSyncRequest(**body)


def test_step_body_started_at_is_safely_in_the_past():
    """固定用過去時間，不因為測試在什麼時候跑而變成「晚於送出時間 5 分鐘
    以上」的未來時間。"""
    from datetime import datetime, timezone

    body = json.loads(verify.build_step_body())
    started_at = datetime.fromisoformat(body["started_at"].replace("Z", "+00:00"))
    assert started_at < datetime.now(timezone.utc)


def test_health_and_system_fields_unchanged():
    """12.1／12.2 只新增健康資料的檢查，不該動到既有的欄位遮蔽守門清單。"""
    assert "age" in verify.HEALTH_AND_SYSTEM_FIELDS
    assert "settings" in verify.HEALTH_AND_SYSTEM_FIELDS
