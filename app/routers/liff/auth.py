from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.services.liff.auth_service import LiffAuthApplicationService
from app.dependencies import get_liff_auth_application_service

router = APIRouter()


class LiffLoginRequest(BaseModel):
    id_token: str = Field(..., min_length=1)


class LiffLoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    line_user_id: str


@router.post(
    "/liff/login",
    response_model=LiffLoginResponse,
    summary="LIFF 登入",
    description="前端送 LIFF ID token，後端向 LINE verify endpoint 驗證後，簽發應用內 JWT 給前端後續 API 使用。",
)
async def liff_login(
    req: LiffLoginRequest,
    service: LiffAuthApplicationService = Depends(get_liff_auth_application_service),
):
    """
    前端送 LIFF ID token，後端向 LINE verify endpoint 驗證後，
    簽發應用內 JWT 給前端後續 API 使用。
    """
    result = await service.login_with_id_token(req.id_token)
    return LiffLoginResponse(**result)
