"""Rich Menu ID 解析與使用者 link。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import requests

from app.core.config import PROJECT_ROOT, settings
from app.services.line_messaging.rich_menu_layout import normalize_rich_menu_language

logger = logging.getLogger(__name__)

RICH_MENU_IDS_PATH = PROJECT_ROOT / "resources" / "rich_menu_ids.json"


def load_rich_menu_ids() -> dict[str, str]:
    raw = settings.RICH_MENU_IDS_JSON
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("RICH_MENU_IDS_JSON is not valid JSON; ignoring")
            return {}
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        logger.warning("RICH_MENU_IDS_JSON must be a JSON object; ignoring")
        return {}

    if not RICH_MENU_IDS_PATH.is_file():
        return {}

    try:
        data = json.loads(RICH_MENU_IDS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read rich menu IDs file: %s", exc)
        return {}

    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items()}
    logger.warning("rich_menu_ids.json must be a JSON object; ignoring")
    return {}


class RichMenuService:
    """依 language 解析 Rich Menu ID 並 link 至 LINE 使用者。"""

    def __init__(
        self,
        get_access_token: Callable[[], str],
        menu_ids: dict[str, str] | None = None,
        http_post: Callable[..., Any] = requests.post,
    ) -> None:
        self._get_access_token = get_access_token
        self._menu_ids = menu_ids if menu_ids is not None else load_rich_menu_ids()
        self._http_post = http_post

    def resolve_menu_id(self, language: str | None) -> str | None:
        lang = normalize_rich_menu_language(language)
        menu_id = self._menu_ids.get(lang)
        if menu_id:
            return menu_id
        return None

    def link_user_menu(self, user_id: str, language: str | None) -> bool:
        menu_id = self.resolve_menu_id(language)
        if not menu_id:
            logger.warning(
                "No rich menu ID for language=%s; skipping link for user=%s",
                language,
                user_id,
            )
            return False

        try:
            access_token = self._get_access_token()
        except Exception as exc:
            logger.warning(
                "Failed to get LINE access token for rich menu link: %s", exc
            )
            return False

        url = f"https://api.line.me/v2/bot/user/{user_id}/richmenu/{menu_id}"
        try:
            response = self._http_post(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.warning("Rich menu link request failed: %s", exc)
            return False

        if response.status_code != 200:
            logger.warning(
                "Rich menu link returned non-200: status=%s body=%s",
                response.status_code,
                getattr(response, "text", ""),
            )
            return False

        return True
