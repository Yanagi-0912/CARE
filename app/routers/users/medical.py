from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.dependencies import CurrentUser, get_current_user, get_medical_service
from app.schemas import MedicalFacility
from app.services.medical.medical_service import MedicalService

router = APIRouter()


class NearbyHospitalsResponse(BaseModel):
    facilities: list[MedicalFacility] = Field(description="附近醫療院所列表")
    count: int = Field(description="回傳筆數")


@router.get(
    "/nearby",
    response_model=NearbyHospitalsResponse,
    summary="依經緯度搜尋附近醫療院所",
    description="LIFF 透過瀏覽器 Geolocation 取得座標後呼叫此 API。",
)
async def get_nearby_hospitals(
    lat: Annotated[float, Query(ge=-90, le=90, description="緯度")],
    lng: Annotated[float, Query(ge=-180, le=180, description="經度")],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[MedicalService, Depends(get_medical_service)],
    radius_meters: Annotated[
        int, Query(ge=100, le=50_000, description="搜尋半徑（公尺）")
    ] = 5_000,
    limit: Annotated[int, Query(ge=1, le=20, description="最多回傳筆數")] = 5,
) -> NearbyHospitalsResponse:
    try:
        result = await service.find_nearby_hospitals(
            lat=lat,
            lng=lng,
            target_count=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="醫療院所查詢暫時不可用，請稍後再試",
        ) from exc

    # find_nearby_hospitals 一律搜到 50 公里（分級只影響「搜到多遠」的文案），
    # 但 LIFF 上的「附近醫院」是使用者明確指定的半徑，超出範圍的不該出現在地圖上。
    # $geoNear 已由近到遠排序，因此截掉超距的等同取半徑內最近的 limit 筆。
    facilities = [
        f
        for f in result.facilities
        if f.distance_meters is None or f.distance_meters <= radius_meters
    ]

    return NearbyHospitalsResponse(facilities=facilities, count=len(facilities))
