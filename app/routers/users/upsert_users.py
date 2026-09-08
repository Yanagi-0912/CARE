from fastapi import APIRouter, Depends, HTTPException

from app.models.user import ProxyHealthUpdate, UserProfileData, UserSettingsUpdate
from app.services.users.user_profile_service import UserProfileService
from app.models.family_authorization import PROXY_WRITE_FORBIDDEN_FIELDS
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)
from app.dependencies import (
    get_user_profile_service,
    get_family_authorization_service,
    get_current_user,
    get_prescription_scan_enabled,
    CurrentUser,
)
from fastapi import HTTPException

router = APIRouter(tags=["Profile"])


@router.get(
    "/me",
    summary="取得目前登入使用者個人健康資料",
    description="回傳目前登入使用者的健康資料，需要有效的 JWT 認證令牌。",
)
async def get_user_profile(
    current_user: CurrentUser = Depends(get_current_user),
    service: UserProfileService = Depends(get_user_profile_service),
):
    """
    取得目前登入使用者個人健康資料。
    """
    user_id = current_user.line_user_id
    profile = await service.get_user_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="找不到使用者資料")
    return profile


@router.put(
    "/me/update",
    summary="更新目前登入使用者個人健康資料",
    description="更新或建立目前登入使用者的健康資料，需要帶上有效的 JWT 認證令牌。",
)
async def upsert_user_profile(
    body: UserProfileData,
    current_user: CurrentUser = Depends(get_current_user),
    service: UserProfileService = Depends(get_user_profile_service),
):
    """
    更新目前登入使用者個人健康資料。
    """
    user_id = current_user.line_user_id
    updated = await service.upsert_user_profile(user_id, body.model_dump())
    return {"user_id": user_id, "updated": updated}


@router.get(
    "/me/settings",
    summary="取得目前登入使用者的介面偏好設定",
    description=(
        "回傳目前登入使用者的介面偏好設定，若資料庫尚無資料（例如舊帳號）"
        "則回傳預設值，不會回傳 404。另外附上 prescription_scan_enabled，"
        "讓 LIFF 能在渲染畫面之前就知道藥袋掃描功能是否開啟，不必再靠"
        "探測其他端點的錯誤訊息旁敲側擊。"
    ),
)
async def get_user_settings(
    current_user: CurrentUser = Depends(get_current_user),
    service: UserProfileService = Depends(get_user_profile_service),
    prescription_scan_enabled: bool = Depends(get_prescription_scan_enabled),
):
    """
    取得目前登入使用者的介面偏好設定。
    """
    user_id = current_user.line_user_id
    user_settings = await service.get_user_settings(user_id)
    return {
        "user_id": user_id,
        "settings": user_settings,
        "prescription_scan_enabled": prescription_scan_enabled,
    }


@router.patch(
    "/me/settings",
    summary="更新目前登入使用者的介面偏好設定",
    description=(
        "部分更新目前登入使用者的介面偏好設定（字體大小、高對比、通知、語音回覆等），"
        "只會更新有帶入的欄位，回傳更新後的完整設定。"
    ),
)
async def update_user_settings(
    body: UserSettingsUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: UserProfileService = Depends(get_user_profile_service),
):
    """
    更新目前登入使用者的介面偏好設定。
    """
    user_id = current_user.line_user_id
    settings = await service.update_user_settings(user_id, body)
    return {"user_id": user_id, "settings": settings}


@router.get(
    "/{userId}",
    summary="取得指定使用者的健康資料",
    description="取得指定使用者的健康資料。出於安全考量，請求者與目標使用者必須在同一個家庭族譜內。",
)
async def get_member_profile(
    userId: str,
    current_user: CurrentUser = Depends(get_current_user),
    profile_service: UserProfileService = Depends(get_user_profile_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    """
    取得指定使用者的健康資料。

    授權一律經 `FamilyAuthorizationService`，這裡不自行判斷「他是不是家人」。

    **無 SENSITIVE 讀取權但在族譜內時回 200，只帶 GENERAL 欄位**（顯示名稱與
    頭像），不是 403。403 會讓前端誤以為連這個人是誰都不能知道，但族譜清單上
    明明就顯示著他的名字——那會逼前端把「沒有權限」與「載入失敗」混為一談。

    遮蔽由後端的序列化邊界執行，未登記的欄位一律不輸出（fail-closed）。
    """
    requester_id = current_user.line_user_id

    # 查自己：不經授權判定，也不遮蔽。新增一個還沒登記的欄位不該讓本人看不到
    # 自己的資料。
    if requester_id == userId:
        profile = await profile_service.get_user_profile(userId)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到使用者資料")
        return profile

    # 先確認至少有 GENERAL 讀取權（即：在對方族譜內）。連這個都沒有就是 403。
    await authz.authorize(requester_id, userId, "GENERAL", "READ")

    profile = await profile_service.get_user_profile(userId)
    if not profile:
        raise HTTPException(status_code=404, detail="找不到該成員的健康資料")

    return await authz.mask_response(
        profile, "health_profile", requester_id, userId
    )


@router.put(
    "/{userId}",
    summary="代為更新指定使用者的健康資料",
    description=(
        "供 GUARDIAN 代被照顧者填寫健康資料。需要對該使用者的 SENSITIVE 分類"
        "具備寫入權；CAREGIVER 僅有讀取權，呼叫此端點會被拒絕。"
        "顯示名稱與頭像不在此路徑的可寫範圍內。"
    ),
)
async def proxy_upsert_user_profile(
    userId: str,
    body: ProxyHealthUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: UserProfileService = Depends(get_user_profile_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    """代為更新他人的健康資料。

    `has_legacy_equivalent=False`：這條路徑在本能力導入**前不存在**。「行為與
    導入前相同」對它而言就是「沒有這個能力」，因此一律依矩陣判定，不受影子
    模式放寬。若沿用 legacy（在族譜裡即放行），一位 MEMBER 會在遷移期間取得
    代寫長輩健康資料的權限——而他在強制之後反而沒有。

    路徑參數帶得出 `userId`，SHALL NOT 因此構成任何允許的依據。
    """
    operator_id = current_user.line_user_id

    if operator_id == userId:
        # 寫自己的資料走既有的 /me/update，語意清楚且不必經過代理判定。
        raise HTTPException(
            status_code=400,
            detail="更新自己的健康資料請使用 PUT /api/profiles/me/update",
        )

    await authz.authorize(
        operator_id, userId, "SENSITIVE", "WRITE", has_legacy_equivalent=False
    )

    # exclude_unset 是這條路徑的正確語意，也是安全上的必要條件：欄位全部可選，
    # 若連沒帶到的鍵也一起 dump 出來，它們會以 None 進 `$set`，把被照顧者既有的
    # 身高、慢性病、病史一次清成 null——呼叫端只想補一個年齡。
    payload = body.model_dump(exclude_unset=True)
    # 分類回答「誰看得到」，不回答「誰改得動」。顯示名稱與頭像雖是 GENERAL，
    # 但要改得經由獨立的 profile-management 授權——代理寫入一律不動它們，
    # 也不動系統角色與介面偏好。
    forbidden = sorted(set(payload) & PROXY_WRITE_FORBIDDEN_FIELDS)
    for field in forbidden:
        payload.pop(field, None)

    # 走部分更新，不是 upsert_user_profile：那支會以 UserProfile 重建完整模型，
    # 而代理寫入刻意不帶 name（必填）→ ValidationError；且重建會把 picture_url
    # 與 settings 補成預設值一起寫回去，清掉被照顧者的頭像與介面偏好。
    updated = await service.update_health_fields(userId, payload)
    return {"user_id": userId, "updated": updated, "skipped_fields": forbidden}
