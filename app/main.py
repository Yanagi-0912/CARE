import logging

from fastapi import FastAPI
from app.routers.line.webhook import router as line_router
from app.routers.system import router as system_router
from app.routers.users.upsert_users import router as profile_router
from app.routers.family_tree import router as family_tree_router
from app.core.cors import add_cors_middleware

logging.basicConfig(level=logging.INFO)

app = FastAPI()

app.include_router(system_router)
app.include_router(
    line_router,
    prefix="/line",
    tags=["LINE Bot"],
)
app.include_router(profile_router, prefix="/profiles", tags=["Profile"])
app.include_router(family_tree_router, prefix="/api/family-tree", tags=["Family Tree"])
