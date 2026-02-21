from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from app.services.gemini_service import GeminiService
from app.schemas import AIRequest, AIResponse, ErrorResponse, HealthResponse, RootResponse
from app.routers.line.webhook import router as line_router
from typing import Union


app = FastAPI(
    title="CARE API",
    description="""
    ## Clinical Assistance & Resource Engine
    
    一個以高齡友善設計、資料準確性保障與 AI 科技整合為核心目標的適地性健康醫療資訊 AI 助手。
    
    ### 主要功能
    
    * **AI 問答服務**：使用 Google Gemini AI 提供智慧型醫療健康諮詢
    * **健康檢查**：檢查服務運行狀態
    
    ### 技術規格
    
    * **框架**：FastAPI
    * **AI 模型**：Google Gemini 2.0 Flash
    * **Python 版本**：3.13+
    """,
    version="1.0.0",
    contact={
        "name": "CARE Team",
    },
    license_info={
        "name": "MIT",
    },
)
app.include_router(line_router, prefix="/api/v1")

gemini_service = GeminiService()




@app.get(
    "/",
    response_model=RootResponse,
    tags=["系統"],
    summary="根路徑",
    description="檢查 API 是否正常運行"
)
async def root():
    """
    返回系統運行狀態訊息。
    
    - **message**: 系統運行狀態
    """
    return {"message": "CARE Backend Running"}

@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["系統"],
    summary="健康檢查",
    description="檢查服務健康狀態"
)
async def health():
    """
    健康檢查端點，用於監控服務是否正常運行。
    
    - **status**: 服務狀態訊息
    """
    return {"status": "Welcome to CARE Backend!"}

@app.post(
    "/api/v1/ai_response",
    responses={
        200: {
            "description": "成功獲得 AI 回應或返回錯誤訊息",
            "content": {
                "application/json": {
                    "examples": {
                        "success": {
                            "summary": "成功案例",
                            "value": {"response": "預防流感的方法包括：1. 接種流感疫苗..."}
                        },
                        "error": {
                            "summary": "錯誤案例",
                            "value": {"error": "user_input is required"}
                        }
                    }
                }
            },
        },
        422: {
            "description": "請求參數驗證失敗",
        },
    },
    tags=["AI 服務"],
    summary="AI 問答服務",
    description="使用 Google Gemini AI 回答使用者的醫療健康相關問題"
)
async def ai_response(request: AIRequest) -> Union[AIResponse, ErrorResponse]:
    """
    接收使用者的問題並返回 AI 生成的回應。
    
    ### 請求參數
    
    - **user_input**: 使用者輸入的問題或訊息（必填）
    
    ### 回應格式
    
    - **成功**: 返回包含 AI 回應的 JSON 物件
    - **失敗**: 返回包含錯誤訊息的 JSON 物件
    
    ### 範例
    
    請求：
    ```json
    {
        "user_input": "請告訴我如何預防流感？"
    }
    ```
    
    成功回應：
    ```json
    {
        "response": "預防流感的方法包括：1. 接種流感疫苗..."
    }
    ```
    
    錯誤回應：
    ```json
    {
        "error": "user_input is required"
    }
    ```
    """
    if not request.user_input or not request.user_input.strip():
        return {"error": "user_input is required"}
    
    try:
        response = await gemini_service.generate_response(request.user_input)
        return {"response": response}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": "系統發生未預期的錯誤，請稍後再試"}