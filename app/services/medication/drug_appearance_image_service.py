"""藥丸縮圖的對外 URL 解析。

只做一件事：證號 -> 可讓 LINE 伺服器抓取的縮圖 URL；查無縮圖時回 None，
讓呼叫端安全地退回純文字版面（spec「照片缺席時的降級」）。刻意不接受
DrugCatalogEntry 或任何額外資料——candidates/license_number 是否確定
是呼叫端（草稿、消歧介面）的責任，這裡只負責「這個證號有沒有落地的圖檔」。

三個刻意的設計：

1. **不讀 `image_url`／不連 mcp.fda.gov.tw。** 縮圖是建置期由
   `scripts/build_drug_catalog.py --fetch-images` 下載並提交進 repo 的靜態
   資源（design.md 決策 2、4）；執行期只檢查本機檔案是否存在，讓推播的
   可用性不會被政府主機的存活與否綁住。

2. **檔名走雜湊，不接受注入的識別碼。** `thumbnail_filename` 與
   `scripts/build_drug_catalog.py` 的同名函式各自獨立算同一條規則
   （sha256(證號) 前 16 碼 + .jpg），不 import scripts——Dockerfile 只
   COPY `app` 與 `resources` 進正式映像，scripts/ 是建置期工具，執行期
   import 它會直接讓容器起不來。兩邊各自實作也讓
   `tests/unit/resources/test_drug_appearance_images.py` 的產出物守門
   測試（獨立第三次重算同一條規則）有意義：三處各自實作卻仍一致，
   才真的證明規則穩定。

3. **image_dir／public_base_url／url_path 皆可由呼叫端明講。** 預設讀
   `app.core.config.settings`（正式路徑不必每次帶參數），但測試可以直接
   傳 `tmp_path` 與假的 base url，不必 monkeypatch 全域的 settings 單例。
"""

import hashlib
import logging
from pathlib import Path
from typing import Optional, Union
from urllib.parse import quote

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = ["resolve_drug_appearance_image_url", "thumbnail_filename"]

_HASH_PREFIX_LENGTH = 16


def thumbnail_filename(license_number: str) -> str:
    """證號 -> 縮圖檔名：sha256 前 16 碼 + .jpg。

    不可枚舉、不含證號本身、不帶任何使用者或用藥資訊——spec「靜態圖片
    資源的識別碼」的唯一要求。16 碼十六進位（64 bits）在全庫 6 千多筆
    的規模下，碰撞機率可忽略，且藥證與外觀本身就是公開資料，「知道證號
    的人能算出路徑」不構成額外洩漏（design.md 決策 4）。
    """
    digest = hashlib.sha256(license_number.encode("utf-8")).hexdigest()
    return f"{digest[:_HASH_PREFIX_LENGTH]}.jpg"


def resolve_drug_appearance_image_url(
    license_number: str,
    *,
    image_dir: Optional[Union[str, Path]] = None,
    public_base_url: Optional[str] = None,
    url_path: Optional[str] = None,
) -> Optional[str]:
    """證號 -> 對外縮圖 URL；查無縮圖或缺少必要設定時回傳 None。

    `image_dir`/`public_base_url`/`url_path` 未帶入時分別預設為
    `settings.DRUG_APPEARANCE_IMAGE_DIR`/`settings.PUBLIC_BASE_URL`/
    `settings.DRUG_APPEARANCE_IMAGE_URL_PATH`，正式呼叫端不需要帶任何
    參數；測試以顯式參數餵入 tmp_path，不需要碰全域的 settings 單例。

    寧可回 None 也不回一個可能 404 的 URL：Flex 訊息裡的圖片是 LINE
    伺服器渲染當下才抓，一則帶壞圖的用藥提醒比沒有圖片更糟
    （spec「照片缺席時的降級」）。
    """
    if not license_number or not license_number.strip():
        return None

    resolved_dir = Path(
        image_dir if image_dir is not None else settings.DRUG_APPEARANCE_IMAGE_DIR
    )
    if not resolved_dir.is_dir():
        # 大聲記錄：目錄整個缺席時，症狀是「這批藥都沒有照片」，跟外觀
        # 資料集本來就沒收錄長得一模一樣，沒有這則 log 就沒人會發現是
        # 部署漏了 resources/drug_appearance/，而不是資料本身沒有外觀。
        logger.error(
            "藥丸縮圖目錄不存在：%s——所有證號都會被視為沒有縮圖，"
            "請確認這次部署是否已落地 resources/drug_appearance/",
            resolved_dir,
        )
        return None

    filename = thumbnail_filename(license_number)
    if not (resolved_dir / filename).is_file():
        return None

    resolved_base_url = (
        public_base_url if public_base_url is not None else settings.PUBLIC_BASE_URL
    )
    if not resolved_base_url.strip():
        logger.warning("PUBLIC_BASE_URL 未設定，無法組出可供 LINE 抓取的藥丸縮圖 URL")
        return None

    resolved_path = (
        url_path if url_path is not None else settings.DRUG_APPEARANCE_IMAGE_URL_PATH
    )
    path_segment = resolved_path.strip("/")
    base = resolved_base_url.rstrip("/")
    if path_segment:
        return f"{base}/{path_segment}/{quote(filename)}"
    return f"{base}/{quote(filename)}"
