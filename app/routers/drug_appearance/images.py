"""藥丸縮圖的靜態服務路徑。

比照 app/routers/tts/tts.py 的作法：不用 StaticFiles 整個目錄掛載，而是
自訂路由逐一驗證檔名格式後才用 FileResponse 回應——這裡的檔名規則是
sha256(證號) 前 16 碼 + .jpg（見 drug_appearance_image_service），驗證
格式同時擋掉路徑穿越（`../`）與任何不屬於這批縮圖的檔案。
"""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings

router = APIRouter(tags=["Drug Appearance"])

IMAGE_NOT_FOUND_DETAIL = "Image not found"
IMAGE_NOT_FOUND_RESPONSE = {
    404: {
        "description": IMAGE_NOT_FOUND_DETAIL,
        "content": {
            "application/json": {
                "example": {"detail": IMAGE_NOT_FOUND_DETAIL},
            }
        },
    }
}

# 16 碼小寫十六進位 + .jpg，對應 thumbnail_filename() 的產出格式。
_FILENAME_PATTERN = re.compile(r"^[0-9a-f]{16}\.jpg$")


@router.get(
    "/{filename}",
    include_in_schema=False,
    responses=IMAGE_NOT_FOUND_RESPONSE,
)
async def get_drug_appearance_image(filename: str):
    # Path(filename).name != filename 擋路徑穿越（例如 "../app/main.py"），
    # 正規表示式再擋住任何不是「雜湊 + .jpg」形狀的請求，兩層都通過才碰檔案系統。
    if Path(filename).name != filename or not _FILENAME_PATTERN.fullmatch(filename):
        raise HTTPException(status_code=404, detail=IMAGE_NOT_FOUND_DETAIL)

    image_path = Path(settings.DRUG_APPEARANCE_IMAGE_DIR) / filename
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail=IMAGE_NOT_FOUND_DETAIL)
    return FileResponse(image_path, media_type="image/jpeg", filename=filename)
