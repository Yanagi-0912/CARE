from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["TTS"])

tts_tmp_dir = Path("app_data") / "tmp"
tts_tmp_dir.mkdir(parents=True, exist_ok=True)

AUDIO_NOT_FOUND_DETAIL = "Audio not found"
TTS_NOT_FOUND_RESPONSE = {
    404: {
        "description": AUDIO_NOT_FOUND_DETAIL,
        "content": {
            "application/json": {
                "example": {"detail": AUDIO_NOT_FOUND_DETAIL},
            }
        },
    }
}


@router.get(
    "/{filename}",
    include_in_schema=False,
    responses=TTS_NOT_FOUND_RESPONSE,
)
async def get_tts_audio(filename: str):
    if not filename.startswith("tts_") or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail=AUDIO_NOT_FOUND_DETAIL)
    if Path(filename).suffix.lower() != ".mp3":
        raise HTTPException(status_code=404, detail=AUDIO_NOT_FOUND_DETAIL)

    audio_path = tts_tmp_dir / filename
    if not audio_path.is_file():
        raise HTTPException(status_code=404, detail=AUDIO_NOT_FOUND_DETAIL)
    return FileResponse(audio_path, media_type="audio/mpeg", filename=filename)
