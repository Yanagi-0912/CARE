from fastapi import APIRouter

from app.schemas import AIRequest, AIResponse, ErrorResponse
from app.services.gemini_service import GeminiService

#給前端(line bot)用的 gemini HTTP 入口，只有一個 endpoint：POST /api/v1/ai_response
router = APIRouter(prefix="/api/v1", tags=["Gemini"])#建立一個API router

@router.post(
    "/ai_response",
    response_model=AIResponse | ErrorResponse,
    summary="Gemini AI 回應",
    description="依 user_input 取得 Gemini AI 回覆",
)
#fastapi會自動幫你把json轉成這個model 轉乘 body.user_input 字串
async def ai_response(body: AIRequest):#告訴body型別是airequest 型別是 AIRequest(這個型別要去schemas.py 找)
    if not (body.user_input or "").strip():
        return ErrorResponse(error="user_input is required")

    svc = GeminiService()
    try:
        text = await svc.generate_response(body.user_input)
        return AIResponse(response=text)
    except ValueError as e:
        return ErrorResponse(error=str(e))

