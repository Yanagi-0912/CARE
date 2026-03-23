from fastapi import FastAPI
from app.routers.line.webhook import router as line_router
from app.routers.system import router as system_router

app = FastAPI()

app.include_router(system_router)
app.include_router(
    line_router,
    prefix="/line",
    tags=["LINE Bot"],
)
