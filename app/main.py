from fastapi import FastAPI
from app.schemas import RootResponse
from app.routers.line.webhook import router as line_router


app = FastAPI(
    title="CARE API",
    description="""
    ## Clinical Assistance & Resource Engine
    
    一個以高齡友善設計、資料準確性保障與 AI 科技整合為核心目標的適地性健康醫療資訊 AI 助手。
    
    ### 主要功能
    
    * **LINE Bot 服務**：透過 LINE Messaging API 提供 AI 智慧對話
    * **AI 智能回覆**：使用 Google Gemini AI 提供健康醫療諮詢
    
    ### 技術規格
    
    * **框架**：FastAPI
    * **AI 模型**：Google Gemini 2.0 Flash
    * **聊天平台**：LINE Messaging API
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
app.include_router(line_router, prefix="/line", tags=["LINE Bot"])

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
