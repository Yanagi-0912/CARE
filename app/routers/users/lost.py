"""走失求救即時位置分享 API（LIFF）。

長輩端（/me）：定位頁上傳位置、查自己是否還在分享、按「我已經安全了」。
家人端（/{user_id}）：地圖頁輪詢位置、按「已找到」。

家人端的授權不走 `authorize`（資料分類矩陣），而是「是不是這位長輩走失通報的
收件人」——見 lost_location_service 的模組說明。位置不是健康資料，看得到
地圖的人必須正好是收到通報的人。沒權限回 403、沒有紀錄回 404，分開兩個狀態碼，
前端才說得出「你沒有權限」與「目前沒有位置分享」的差別。

求救只能從 LINE 對話開始（說「我走丟了」），這裡沒有「開始分享」的端點：
開始的那一刻要帶著長輩的原話通知家人，定位頁沒有那句話。
"""

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_lost_location_service,
    lost_location_rate_limit,
)
from app.models.lost_location import (
    LostEndResult,
    LostLocationPoint,
    LostLocationUpload,
    LostSelfStatus,
    LostSessionView,
    LostTrailPoint,
)
from app.repositories.lost_session_repository import as_utc
from app.services.lost.lost_location_service import STALE_AFTER, LostLocationService

router = APIRouter()

FORBIDDEN_DETAIL = "您沒有權限查看這位家人的位置。"
NOT_FOUND_DETAIL = "目前沒有位置分享。"


def _self_status(session: Optional[dict[str, Any]]) -> LostSelfStatus:
    if session is None:
        return LostSelfStatus(active=False)
    return LostSelfStatus(
        active=session.get("status") == "active",
        status=session.get("status"),
        started_at=as_utc(session.get("started_at")),
        ended_at=as_utc(session.get("ended_at")),
    )


@router.get("/me", response_model=LostSelfStatus, summary="我目前的位置分享狀態")
async def my_status(
    current_user: CurrentUser = Depends(get_current_user),
    service: LostLocationService = Depends(get_lost_location_service),
) -> LostSelfStatus:
    return _self_status(await service.latest(current_user.line_user_id))


@router.post(
    "/me/location",
    response_model=LostSelfStatus,
    summary="上傳目前位置",
    description="只有進行中的求救會記錄；已結束或從沒開始時不存位置，回 active=false。",
    dependencies=[Depends(lost_location_rate_limit)],
)
async def upload_location(
    body: LostLocationUpload,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    service: LostLocationService = Depends(get_lost_location_service),
) -> LostSelfStatus:
    user_id = current_user.line_user_id
    session = await service.record_location(
        user_id,
        lat=body.latitude,
        lng=body.longitude,
        accuracy=body.accuracy,
        source="liff",
    )
    if session is None:
        return _self_status(await service.latest(user_id))
    if service.needs_location_started_notice(session):
        # 推給每位家人要各打一次 LINE API，不讓長輩的上傳請求等它們。
        background_tasks.add_task(service.notify_location_started, session)
    return _self_status(session)


@router.post("/me/end", response_model=LostEndResult, summary="長輩表示已經安全")
async def end_by_elder(
    current_user: CurrentUser = Depends(get_current_user),
    service: LostLocationService = Depends(get_lost_location_service),
) -> LostEndResult:
    session = await service.end_by_elder(current_user.line_user_id)
    if session is None:
        return LostEndResult(ended=False)
    return LostEndResult(ended=True, status=session["status"])


async def _authorize_viewer(
    service: LostLocationService, operator_id: str, owner_id: str
) -> None:
    if not await service.can_view(operator_id, owner_id):
        raise HTTPException(status_code=403, detail=FORBIDDEN_DETAIL)


@router.get(
    "/{user_id}",
    response_model=LostSessionView,
    summary="家人查看即時位置",
    description="最近一次位置分享（結束後 24 小時內仍查得到最後位置）。",
)
async def view_session(
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: LostLocationService = Depends(get_lost_location_service),
) -> LostSessionView:
    await _authorize_viewer(service, current_user.line_user_id, user_id)
    session = await service.latest(user_id)
    if session is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL)

    ended_by = session.get("ended_by")
    ended_by_name = None
    if session.get("status") == "found" and ended_by:
        ended_by_name = await service.display_name(ended_by) or None

    location = session.get("last_location")
    return LostSessionView(
        session_id=session["session_id"],
        status=session["status"],
        intent=session.get("intent") or "lost",
        patient_name=await service.display_name(user_id),
        patient_words=session.get("patient_words") or "",
        started_at=as_utc(session["started_at"]),
        auto_end_at=as_utc(session["auto_end_at"]),
        ended_at=as_utc(session.get("ended_at")),
        ended_by_name=ended_by_name,
        last_location=(
            LostLocationPoint(
                latitude=location["lat"],
                longitude=location["lng"],
                accuracy=location.get("accuracy"),
                source=location.get("source"),
                received_at=as_utc(location["received_at"]),
            )
            if location
            else None
        ),
        last_seen_at=as_utc(session.get("last_seen_at")),
        stale=service.is_stale(session),
        stale_after_seconds=int(STALE_AFTER.total_seconds()),
        trail=[
            LostTrailPoint(latitude=p["lat"], longitude=p["lng"], at=as_utc(p["at"]))
            for p in session.get("trail") or []
        ],
        server_time=datetime.now(timezone.utc),
    )


@router.post("/{user_id}/found", response_model=LostEndResult, summary="家人表示已找到")
async def mark_found(
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: LostLocationService = Depends(get_lost_location_service),
) -> LostEndResult:
    operator_id = current_user.line_user_id
    await _authorize_viewer(service, operator_id, user_id)
    session = await service.end_by_family(user_id, operator_id)
    if session is None:
        # 別人先按了、或已經自動結束：不是錯誤，前端照最新狀態顯示即可。
        latest = await service.latest(user_id)
        return LostEndResult(ended=False, status=(latest or {}).get("status"))
    return LostEndResult(ended=True, status=session["status"])
