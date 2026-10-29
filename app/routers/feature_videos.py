"""功能說明操作示範影片的靜態服務路徑（說明見 app/services/line_messaging/feature_videos.py）。

比照 app/routers/drug_appearance/images.py：不用 StaticFiles 整個目錄掛載，
語言與檔名都先比對白名單才碰檔案系統，順便擋掉路徑穿越。

FileResponse 會處理 Range 標頭（回 206）。iPhone 的播放器要能分段讀取才肯
播 mp4，LINE 抓影片時也會帶 Range。
"""

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.line_messaging import feature_videos

router = APIRouter(tags=["Feature Videos"])

NOT_FOUND_DETAIL = "Video not found"

_MEDIA_TYPES = {
    feature_videos.VIDEO_EXT: "video/mp4",
    feature_videos.PREVIEW_EXT: "image/jpeg",
}
_FILENAME_PATTERN = re.compile(
    rf"^(?P<section>[a-z]+)\.(?P<ext>{'|'.join(_MEDIA_TYPES)})$"
)


@router.get("/{language}/{filename}", include_in_schema=False)
async def get_feature_video(language: str, filename: str):
    match = _FILENAME_PATTERN.fullmatch(filename)
    if not match or not feature_videos.is_known(language, match["section"]):
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL)

    path = feature_videos.file_path(language, match["section"], match["ext"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail=NOT_FOUND_DETAIL)
    return FileResponse(path, media_type=_MEDIA_TYPES[match["ext"]])
