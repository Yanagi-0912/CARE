import json
import logging
from typing import Any

from langchain_core.tools import tool

from resources.flex_messages.official_site_flex_message import (
    generate_official_site_flex_message,
)

logger = logging.getLogger(__name__)

_liff_url: str = ""
_public_base_url: str = ""

EMPTY_URLS_MESSAGE = "官方入口尚未設定，請稍後再試。"


def configure_official_site_tool(
    *, liff_url: str = "", public_base_url: str = ""
) -> None:
    """DI 初始化時呼叫，注入 LIFF 與官網 URL。"""
    global _liff_url, _public_base_url
    _liff_url = liff_url or ""
    _public_base_url = public_base_url or ""


def _to_flex_message_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


@tool
async def open_official_site() -> str:
    """當使用者要開啟官網、官方網站或 LIFF 入口時呼叫此工具。"""
    liff = _liff_url.strip()
    public = _public_base_url.strip()
    if not liff and not public:
        logger.warning("open_official_site called but LIFF_URL and PUBLIC_BASE_URL are empty")
        return EMPTY_URLS_MESSAGE

    return _to_flex_message_text(
        generate_official_site_flex_message(liff, public)
    )
