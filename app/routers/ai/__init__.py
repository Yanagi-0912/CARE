from fastapi import APIRouter

from app.routers.ai import gemini_routes

router = APIRouter()
router.include_router(gemini_routes.router)
