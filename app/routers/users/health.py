"""個人健康紀錄的對外端點（``/api/health``）。

Task 3 實作了提醒範圍（``GET``／``PUT /alert-thresholds``）；Task 4 在這裡
接著加血壓／血糖量測（``POST``／``GET``／``DELETE /measurements``）。
Task 5（經期、計步）、Task 6（其餘章節）會繼續往下加各自的區塊，請沿用下面
「── 區塊名稱 ──」的分隔慣例，新增的路徑一律接在檔尾。

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

from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, Query, status

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_family_authorization_service,
    get_health_alert_threshold_service,
    get_health_measurement_service,
)
from app.models.health import (
    CreateBloodGlucoseRequest,
    CreateBloodPressureRequest,
    HealthAlertThreshold,
    HealthMeasurement,
    MeasurementKind,
    UpdateHealthAlertThresholdRequest,
)
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)
from app.services.health.health_alert_threshold_service import (
    HealthAlertThresholdService,
)
from app.services.health.health_measurement_service import HealthMeasurementService

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


# ── POST／GET／DELETE /api/health/measurements ───────────────────────────
#
# health-measurements spec「記錄血壓」「記錄血糖」「代為記錄」「查看紀錄」
# 「刪除紀錄」。授權原則與上面提醒範圍相同：本人一律放行，他人一律
# `authorize(..., has_legacy_equivalent=False)`——這三支端點在本 change
# 導入前不存在，不受影子模式放寬。
#
# 全部欄位皆登記為 SENSITIVE（見 `app/models/family_authorization.py`），
# `authorize` 一旦放行即可看到整筆紀錄，因此不呼叫 `mask()`／`mask_response()`
# ——同提醒範圍端點的理由。


@router.post(
    "/measurements",
    response_model=HealthMeasurement,
    response_model_by_alias=False,
    status_code=status.HTTP_201_CREATED,
    summary="新增一筆血壓或血糖紀錄",
    description=(
        "以血壓（systolic／diastolic／pulse）或血糖（glucose_mg_dl／"
        "meal_context）擇一送出 body，依欄位自動判斷種類。measured_at "
        "省略時為送出當下；等級由本人當下的提醒範圍計算並隨紀錄儲存，"
        "省略 user_id 時為本人。"
    ),
)
async def create_measurement(
    body: Union[CreateBloodPressureRequest, CreateBloodGlucoseRequest],
    user_id: Optional[str] = Query(
        default=None, description="要記錄的使用者 LINE userId，省略則為本人"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    service: HealthMeasurementService = Depends(get_health_measurement_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
) -> HealthMeasurement:
    """新增血壓／血糖紀錄（health-measurements spec「代為記錄」）。

    代記需要操作者對本人的 SENSITIVE 資料具寫入權（依現行矩陣只有
    GUARDIAN），且必須在寫入之前完成——403 時 SHALL NOT 留下任何紀錄。
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

    return await service.create(
        user_id=target_user_id, recorded_by=operator_id, request=body
    )


@router.get(
    "/measurements",
    response_model=List[HealthMeasurement],
    response_model_by_alias=False,
    summary="查詢血壓血糖紀錄",
    description=(
        "查詢本人或指定使用者的血壓血糖紀錄，可依 kind 篩選種類、依 start／"
        "end（量測時間，ISO-8601）篩選區間。兩者皆省略時回傳最近 30 天。"
        "結果依量測時間新到舊排序，單次回應至多 200 筆。"
    ),
)
async def list_measurements(
    user_id: Optional[str] = Query(
        default=None, description="要查詢的使用者 LINE userId，省略則為本人"
    ),
    kind: Optional[MeasurementKind] = Query(
        default=None, description="血壓或血糖，省略則兩者皆回傳"
    ),
    # start／end 若帶不含時區的時間，FastAPI／Pydantic 解析成 naive datetime，
    # 直接送進 PyMongo 的 $gte／$lte 比較——這與 measured_at 本身回讀時的形狀
    # 一致（見 app/models/health.py 的 _reject_far_future 說明：Motor client
    # 未啟用 tz_aware，naive datetime 經資料庫存取一圈後其實就是 UTC）。因此
    # 這裡刻意不像 POST 對 body 那樣把 naive 值正規化成帶時區的 UTC：兩邊比較
    # 的都是同一種「naive＝UTC」的表示，正規化其中一邊反而會讓比較不一致。
    start: Optional[datetime] = Query(
        default=None, description="量測時間下限（含），省略且 end 也省略時預設最近 30 天"
    ),
    end: Optional[datetime] = Query(
        default=None, description="量測時間上限（含），省略且 start 也省略時預設查詢當下"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    service: HealthMeasurementService = Depends(get_health_measurement_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
) -> List[HealthMeasurement]:
    """查詢血壓血糖紀錄（health-measurements spec「查看紀錄」）。

    他人查看需 SENSITIVE 讀取權（依現行矩陣：GUARDIAN、CAREGIVER 可看，
    MEMBER 與非家人不行），`has_legacy_equivalent=False`。
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

    return await service.list(user_id=target_user_id, kind=kind, start=start, end=end)


@router.delete(
    "/measurements/{measurement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="刪除一筆血壓或血糖紀錄",
    description="刪除指定的血壓或血糖紀錄。刪除不會撤回已經送出的通知。",
)
async def delete_measurement(
    measurement_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: HealthMeasurementService = Depends(get_health_measurement_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
) -> None:
    """刪除一筆紀錄（health-measurements spec「刪除紀錄」）。

    存在性判定在授權之前：紀錄不存在一律 404，即使操作者對本人沒有任何
    權限也一樣——不讓 403／404 的差異被拿來探測他人紀錄是否存在。
    存在之後，本人或對本人 SENSITIVE 資料具寫入權者（依現行矩陣只有
    GUARDIAN）才能刪除；`has_legacy_equivalent=False`。
    """
    measurement = await service.get(measurement_id)
    operator_id = current_user.line_user_id

    if operator_id != measurement.user_id:
        await authz.authorize(
            operator_id,
            measurement.user_id,
            "SENSITIVE",
            "WRITE",
            has_legacy_equivalent=False,
        )

    await service.delete(measurement_id)
