import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from app.routers.line.webhook import router as line_router
from app.routers.liff.auth import router as auth_router
from app.routers.system import router as system_router
from app.routers.users.upsert_users import router as profile_router
from app.core.cors import add_cors_middleware

from app.routers.family_tree import router as family_tree_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="CARE Backend API",
    description="CARE 系統後端 API (包含 LINE Bot Webhook 與 LIFF REST API)",
    version="1.0.0",
)

tts_tmp_dir = Path("app_data") / "tmp"
tts_tmp_dir.mkdir(parents=True, exist_ok=True)


@app.get("/tts/{filename}", include_in_schema=False)
async def get_tts_audio(filename: str):
    if not filename.startswith("tts_") or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Audio not found")
    if Path(filename).suffix.lower() != ".mp3":
        raise HTTPException(status_code=404, detail="Audio not found")

    audio_path = tts_tmp_dir / filename
    if not audio_path.is_file():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(audio_path, media_type="audio/mpeg", filename=filename)

# Centralized CORS config
add_cors_middleware(app)
app.include_router(system_router)
app.include_router(line_router, prefix="/line", tags=["LINE Bot"])
app.include_router(profile_router, prefix="/api/profiles", tags=["Profile"])
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(family_tree_router, prefix="/api/family", tags=["Family Tree"])
