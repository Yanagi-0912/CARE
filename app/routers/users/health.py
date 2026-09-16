"""個人健康紀錄的對外端點（``/api/health``）。

Task 3 只實作提醒範圍（``GET``／``PUT /alert-thresholds``）；Task 4（血壓／
血糖量測）、Task 5（經期、計步）、Task 6（其餘章節）會在這支檔案繼續往下
加各自的區塊，請沿用下面「── 區塊名稱 ──」的分隔慣例，新增的路徑一律接在
檔尾。

授權原則（constraints.md「Authorization」；health-alerts spec「誰能設定與
查看提醒範圍」）：

- 本人一律放行，不經 ``FamilyAuthorizationService``——查看／設定自己的資料
  不該因為族譜裡沒有自己這個節點而出錯。
- 他人存取一律呼叫 ``authorize(..., has_legacy_equivalent=False)``：這些
  端點在本 change 導入前不存在，「與導入前相同」的意思是**沒有這個能力**，
  因此不受影子模式放寬（見 ``app/routers/users/medications.py``、
  ``app/routers/users/upsert_users.py`` 的同一套慣例）。
- 提醒範圍整份都是 SENSITIVE（``FIELD_CLASSIFICATION`` 沒有欄位分級），
  一旦 ``authorize`` 放行即可看到全部欄位，因此不需要、也 SHALL NOT 呼叫
  ``mask()``／``mask_response()``——那是給混合分類資源用的。
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_family_authorization_service,
    get_health_alert_threshold_service,
)
from app.models.health import HealthAlertThreshold, UpdateHealthAlertThresholdRequest
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)
from app.services.health.health_alert_threshold_service import (
    HealthAlertThresholdService,
)

router = APIRouter()


# ── GET／PUT /api/health/alert-thresholds ────────────────────────────────


@router.get(
    "/alert-thresholds",
    summary="查看提醒範圍",
    description=(
        "查看本人或指定使用者的血壓／血糖提醒範圍。省略 user_id 時為本人。"
        "從未設定過時仍回傳 200，七項範圍與 updated_by/updated_at 皆為 null，"
        "不會回 404。"
    ),
)
async def get_alert_thresholds(
    user_id: Optional[str] = Query(
        default=None, description="要查詢的使用者 LINE userId，省略則為本人"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    service: HealthAlertThresholdService = Depends(get_health_alert_threshold_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
) -> Dict[str, Any]:
    """查看提醒範圍（health-alerts spec「誰能設定與查看提醒範圍」）。

    他人查看需 SENSITIVE 讀取權（依現行矩陣：GUARDIAN、CAREGIVER 可看，
    MEMBER 與非家人不行），`has_legacy_equivalent=False`——不受遷移狀態
    放寬。
    """
    operator_id = current_user.line_user_id
    target_user_id = user_id or operator_id

    if operator_id != target_user_id:
        await authz.authorize(
            operator_id,
            target_user_id,
            "SENSITIVE",
            "READ",
            has_legacy_equivalent=False,
        )

    return await service.get_view(target_user_id)


@router.put(
    "/alert-thresholds",
    response_model=HealthAlertThreshold,
    summary="設定提醒範圍",
    description=(
        "以整份覆寫設定本人或指定使用者的血壓／血糖提醒範圍。省略 user_id "
        "時為本人；body 省略或帶 null 的欄位視為清除該項。"
    ),
)
async def update_alert_thresholds(
    body: UpdateHealthAlertThresholdRequest,
    user_id: Optional[str] = Query(
        default=None, description="要設定的使用者 LINE userId，省略則為本人"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    service: HealthAlertThresholdService = Depends(get_health_alert_threshold_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
) -> HealthAlertThreshold:
    """設定提醒範圍（health-alerts spec「誰能設定與查看提醒範圍」）。

    他人設定需 SENSITIVE 寫入權（依現行矩陣：只有 GUARDIAN；CAREGIVER 只有
    讀取權，呼叫這支會被拒絕），`has_legacy_equivalent=False`。範圍與上下限
    關係已在 ``UpdateHealthAlertThresholdRequest``（Pydantic 邊界）驗證過，
    不合法直接 422、不寫入。系統保存最後一次修改的人：``updated_by`` 一律
    是操作者，即使是代為設定。
    """
    operator_id = current_user.line_user_id
    target_user_id = user_id or operator_id

    if operator_id != target_user_id:
        await authz.authorize(
            operator_id,
            target_user_id,
            "SENSITIVE",
            "WRITE",
            has_legacy_equivalent=False,
        )

    return await service.update(target_user_id, body, updated_by=operator_id)
