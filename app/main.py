from fastapi import FastAPI

from app.routers.line.webhook import router as line_router
from app.routers.system import router as system_router

# main 只負責建立 app 並掛各模組 router
app = FastAPI()

# main 來決定整個系統的 endpoint 要掛哪個前綴
app.include_router(system_router)
app.include_router(
    line_router,
    prefix="/line",
    tags=["LINE Bot"],
)  # 為啥要寫 prefix 在 line：因為 webhook 裡只管 /callback
