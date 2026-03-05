from fastapi import FastAPI

from app.routers.line.webhook import router as line_router
from app.routers.system import router as system_router

# main 只負責建立 app 並掛各模組 router
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
# main 來決定整個系統的 endpoint 要掛哪個前綴
app.include_router(system_router)
app.include_router(
    line_router,
    prefix="/line",
    tags=["LINE Bot"],
)  # 為啥要寫 prefix 在 line：因為 webhook 裡只管 /callback
