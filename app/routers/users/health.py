"""個人健康紀錄的對外端點（``/api/health``）。

Task 3 實作了提醒範圍（``GET``／``PUT /alert-thresholds``）；Task 4 在這裡
接著加血壓／血糖量測（``POST``／``GET``／``DELETE /measurements``）；Task 5
加經期（``POST``／``GET``／``PATCH``／``DELETE /menstrual``）；Task 6 加計步
（``PUT /steps/sessions/{session_id}``／``GET /steps``）。日後新增的路徑
請沿用下面「── 區塊名稱 ──」的分隔慣例，接在檔尾。

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

from datetime import date, datetime
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import UUID4

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_family_authorization_service,
    get_health_alert_threshold_service,
    get_health_measurement_service,
    get_menstrual_record_service,
    get_step_service,
)
from app.models.health import (
    CreateBloodGlucoseRequest,
    CreateBloodPressureRequest,
    CreateMenstrualRecordRequest,
    HealthAlertThreshold,
    HealthMeasurement,
    MeasurementKind,
    MenstrualRecord,
    StepCount,
    StepSessionSyncRequest,
    UpdateHealthAlertThresholdRequest,
    UpdateMenstrualRecordRequest,
)
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)
from app.services.health.health_alert_threshold_service import (
    HealthAlertThresholdService,
)
from app.services.health.health_measurement_service import HealthMeasurementService
from app.services.health.menstrual_service import MenstrualRecordService
from app.services.health.step_service import StepService

router = APIRouter()

# 經期是 PERSONAL 分類（app/models/family_authorization.py）：只有本人可讀寫，
# 任何家人角色——包括對本人有完整 SENSITIVE 權限的 GUARDIAN、或持有有效
# 委任的人——一律無權，不論家庭的遷移狀態為何（menstrual-cycle-log spec
# 「經期資料不跨使用者呈現」）。以他人識別碼查詢／新增／修改／刪除經期，
# SHALL 一律 403，這個判定 SHALL NOT 經過 FamilyAuthorizationService 的矩陣
# ——矩陣的 PERSONAL 欄本來就整欄是空集合，但更根本的是：這個資源完全不該
# 讓任何非本人的請求走到能查表的地步，因此直接比對操作者與資料所有者的
# 識別碼是否相同，不呼叫 authorize()。
MENSTRUAL_CROSS_USER_DETAIL = "經期紀錄僅限本人查看與異動。"


# ── GET／PUT /api/health/alert-thresholds ────────────────────────────────


@router.get(
    "/alert-thresholds",
    response_model=HealthAlertThreshold,
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
) -> HealthAlertThreshold:
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

    存在性判定在授權之前：這裡要判斷「操作者是否對這筆紀錄的本人有權限」，
    但本人是誰要先把紀錄讀出來才知道——存在性檢查因此先於授權判定，不是
    刻意設計。這個順序的副作用是不存在的 id 回 404、存在但無權限回
    403，等於讓操作者能分辨「這個 id 存不存在」；這裡接受這個副作用，因為
    紀錄 id 不可猜測（隨機產生），單靠這個 404／403 差異探測不出有意義的
    資訊。存在之後，本人或對本人 SENSITIVE 資料具寫入權者（依現行矩陣只有
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


# ── POST／GET／PATCH／DELETE /api/health/menstrual ───────────────────────
#
# menstrual-cycle-log spec「僅女性使用者可建立」「記錄經期」「經期資料不
# 跨使用者呈現」。授權原則與上面兩個區塊不同：經期是 PERSONAL 分類，
# SHALL NOT 呼叫 FamilyAuthorizationService——見檔案頂端
# ``MENSTRUAL_CROSS_USER_DETAIL`` 旁的說明。`user_id` 查詢參數的**唯一**
# 作用是「讓帶了別人 id 的請求可以被判定為 403」，不是拿來代記或代查；
# 一旦不等於操作者本人，一律 403，沒有任何角色可以通過。
#
# 全部欄位皆登記為 PERSONAL（``app/models/family_authorization.py``），
# `authorize` 從不被呼叫，因此也 SHALL NOT 呼叫 `mask()`／`mask_response()`
# ——那兩個函式假設呼叫端已經決定「這是本人還是他人的資料」，這裡的識別碼
# 比對本身就已經是唯一的守門。


def _reject_cross_user_menstrual_access(operator_id: str, user_id: Optional[str]) -> None:
    if user_id is not None and user_id != operator_id:
        raise HTTPException(status_code=403, detail=MENSTRUAL_CROSS_USER_DETAIL)


@router.post(
    "/menstrual",
    response_model=MenstrualRecord,
    response_model_by_alias=False,
    status_code=status.HTTP_201_CREATED,
    summary="新增一筆經期紀錄",
    description=(
        "新增本人的經期紀錄。經期沒有代記，`user_id` 只接受省略或本人的 "
        "LINE userId，帶入他人 id 一律 403。建立限本人個人健康檔案性別為"
        "「女性」，否則 403 並說明需先設定性別。"
    ),
)
async def create_menstrual_record(
    body: CreateMenstrualRecordRequest,
    user_id: Optional[str] = Query(
        default=None, description="僅接受省略或本人的 LINE userId，帶入他人 id 一律 403"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    service: MenstrualRecordService = Depends(get_menstrual_record_service),
) -> MenstrualRecord:
    """新增經期紀錄（menstrual-cycle-log spec「記錄經期」）。

    性別限制、重疊檢查（409）、日期驗證（422）皆在服務層／請求模型處理，
    這裡只負責「這一定是本人的請求」這件事。
    """
    operator_id = current_user.line_user_id
    _reject_cross_user_menstrual_access(operator_id, user_id)

    return await service.create(operator_id, body)


@router.get(
    "/menstrual",
    response_model=List[MenstrualRecord],
    response_model_by_alias=False,
    summary="查詢本人的經期紀錄",
    description=(
        "查詢本人的經期紀錄，依開始日期新到舊排序，單次回應至多 200 筆。"
        "每一筆皆附上後端計算的週期長度（與前一筆開始日期的間隔）與經期"
        "天數（含頭尾兩天）。`user_id` 只接受省略或本人的 LINE userId，"
        "帶入他人 id 一律 403。"
    ),
)
async def list_menstrual_records(
    user_id: Optional[str] = Query(
        default=None, description="僅接受省略或本人的 LINE userId，帶入他人 id 一律 403"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    service: MenstrualRecordService = Depends(get_menstrual_record_service),
) -> List[MenstrualRecord]:
    """查詢本人的經期紀錄（menstrual-cycle-log spec「經期資料不跨使用者
    呈現」：以他人識別碼查詢一律 403）。
    """
    operator_id = current_user.line_user_id
    _reject_cross_user_menstrual_access(operator_id, user_id)

    return await service.list(operator_id)


@router.patch(
    "/menstrual/{record_id}",
    response_model=MenstrualRecord,
    response_model_by_alias=False,
    summary="修正一筆經期紀錄",
    description=(
        "部分更新一筆經期紀錄，未帶到的欄位維持原值；明確帶 "
        "`end_date: null` 代表重新打開這筆紀錄。更新後的整筆內容會重新"
        "驗證（結束不早於開始、間隔不超過 15 天、開始不晚於今天、不與其他"
        "紀錄重疊）。"
    ),
)
async def update_menstrual_record(
    record_id: str,
    body: UpdateMenstrualRecordRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: MenstrualRecordService = Depends(get_menstrual_record_service),
) -> MenstrualRecord:
    """修正一筆經期紀錄（menstrual-cycle-log spec「事後補上結束日期」）。

    存在性判定在先（不存在一律 404），之後才比對操作者與所有者的識別碼——
    同量測「刪除紀錄」的理由：所有者是誰要先讀出紀錄才知道，因此存在性
    檢查必須先於身分比對，而不是為了防堵探測才這樣排序；這個順序讓不存在
    的 id 回 404、存在但非本人回 403，這裡接受這個可分辨的差異，因為紀錄
    id 不可猜測。這筆紀錄若屬於他人，一律 403，SHALL NOT 修改、也 SHALL
    NOT 揭露內容。
    """
    existing = await service.get(record_id)
    operator_id = current_user.line_user_id

    if operator_id != existing.user_id:
        raise HTTPException(status_code=403, detail=MENSTRUAL_CROSS_USER_DETAIL)

    return await service.update(record_id, body)


@router.delete(
    "/menstrual/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="刪除一筆經期紀錄",
    description="刪除指定的經期紀錄。",
)
async def delete_menstrual_record(
    record_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: MenstrualRecordService = Depends(get_menstrual_record_service),
) -> None:
    """刪除一筆經期紀錄（menstrual-cycle-log spec「記錄經期」：「本人 SHALL
    能刪除紀錄」）。存在性與所有權判定同 ``update_menstrual_record``。
    """
    existing = await service.get(record_id)
    operator_id = current_user.line_user_id

    if operator_id != existing.user_id:
        raise HTTPException(status_code=403, detail=MENSTRUAL_CROSS_USER_DETAIL)

    await service.delete(record_id)


# ── PUT／GET /api/health/steps ───────────────────────────────────────────
#
# step-counter spec「只有本人可以寫入」「家人查看步數」。寫入的授權原則與
# 上面經期相同：以他人識別碼寫入一律 403，不經 FamilyAuthorizationService
# ——步數只能來自本人手機的感測器，不存在「代為走路」，沒有任何角色（包含
# 對其他資源有完整讀寫權的 GUARDIAN）能通過，也不受遷移狀態放寬（同經期
# 端點的理由，見該區塊的說明）。讀取則與量測、提醒範圍相同：他人查看需
# SENSITIVE READ（GUARDIAN、CAREGIVER 可看，MEMBER 與非家人 403），
# `has_legacy_equivalent=False`。
#
# 全部欄位皆登記為 SENSITIVE（見 `app/models/family_authorization.py`），
# `authorize` 一旦放行即可看到整筆紀錄，因此不呼叫 `mask()`／`mask_response()`
# ——同量測、提醒範圍端點的理由。

STEP_PROXY_WRITE_DETAIL = "步數只能由本人回報，不支援代為記錄。"


@router.put(
    "/steps/sessions/{session_id}",
    response_model=StepCount,
    summary="同步一個計步工作階段的累計步數",
    description=(
        "回報 session_id 這個工作階段目前的累計步數（不是增量），"
        "session_id 須為前端產生的 UUID v4。只能回報自己的步數，帶入他人 "
        "user_id 一律 403，不論操作者的角色。回應為該工作階段所屬日期"
        "（依 started_at 換算的台北日曆日）的當日總步數。"
    ),
)
async def sync_step_session(
    session_id: UUID4,
    body: StepSessionSyncRequest,
    user_id: Optional[str] = Query(
        default=None, description="僅接受省略或本人的 LINE userId，帶入他人 id 一律 403"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    service: StepService = Depends(get_step_service),
) -> StepCount:
    """同步一次工作階段的累計步數（step-counter spec「只有本人可以寫入」）。

    步數來自本人手機的感測器，不存在「代為走路」，因此 `user_id` 唯一的
    作用是讓帶了別人 id 的請求被判定為 403，不論操作者的角色——同經期端點
    的理由，不呼叫 `FamilyAuthorizationService`。
    """
    operator_id = current_user.line_user_id
    if user_id is not None and user_id != operator_id:
        raise HTTPException(status_code=403, detail=STEP_PROXY_WRITE_DETAIL)

    return await service.sync(
        user_id=operator_id, session_id=str(session_id), request=body
    )


@router.get(
    "/steps",
    response_model=List[StepCount],
    summary="查詢每日步數",
    description=(
        "查詢本人或指定使用者的每日步數，依 start／end（YYYY-MM-DD，台北"
        "日曆日）篩選區間，兩者皆省略時預設最近 7 天（含今天）；區間長度"
        "不得超過 90 天。只回傳有工作階段的日期，新到舊排序。"
    ),
)
async def list_step_counts(
    user_id: Optional[str] = Query(
        default=None, description="要查詢的使用者 LINE userId，省略則為本人"
    ),
    start: Optional[date] = Query(
        default=None, description="起始日期（含，YYYY-MM-DD，台北日曆日）"
    ),
    end: Optional[date] = Query(
        default=None, description="結束日期（含，YYYY-MM-DD，台北日曆日）"
    ),
    current_user: CurrentUser = Depends(get_current_user),
    service: StepService = Depends(get_step_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
) -> List[StepCount]:
    """查詢每日步數（step-counter spec「家人查看步數」）。

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

    return await service.list_daily_totals(target_user_id, start=start, end=end)
