from fastapi import APIRouter

from app.schemas import HealthResponse, RootResponse


router = APIRouter(tags=["系統"])


@router.get(
    "/",
    response_model=RootResponse,
    summary="根路徑",
    description="檢查 API 是否正常運行",
)
async def root():
    return {"message": "CARE Backend Running"}


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="健康檢查",
    description="回傳服務狀態",
)
async def health():
    return {"status": "Welcome to CARE Backend!"}

