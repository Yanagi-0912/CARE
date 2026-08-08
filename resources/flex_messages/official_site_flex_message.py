"""官網／LIFF 入口 Flex Message 產生器。"""

from typing import Any


def _build_uri_button_box(
    label: str,
    uri: str,
    *,
    background_color: str = "#2E7D32",
    text_color: str = "#FFFFFF",
) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": background_color,
        "cornerRadius": "md",
        "paddingAll": "md",
        "flex": 1,
        "action": {
            "type": "uri",
            "label": label,
            "uri": uri,
        },
        "contents": [
            {
                "type": "text",
                "text": label,
                "color": text_color,
                "weight": "bold",
                "size": "20px",
                "align": "center",
            }
        ],
    }


def _collect_uri_buttons(liff_url: str, public_base_url: str) -> list[dict[str, Any]]:
    buttons: list[dict[str, Any]] = []
    liff = liff_url.strip()
    public = public_base_url.strip()

    if liff:
        buttons.append(_build_uri_button_box("開啟 LIFF", liff))
    if public:
        buttons.append(
            _build_uri_button_box(
                "開啟官網",
                public,
                background_color="#E0E0E0",
                text_color="#111111",
            )
        )
    return buttons


def generate_official_site_flex_message(
    liff_url: str, public_base_url: str
) -> dict[str, Any]:
    """依可用 URL 產生官網入口 Flex Message（至少需一個非空 URL）。"""
    buttons = _collect_uri_buttons(liff_url, public_base_url)
    if not buttons:
        raise ValueError("At least one non-empty URL is required")

    button_layout: dict[str, Any] = {
        "type": "box",
        "layout": "horizontal" if len(buttons) > 1 else "vertical",
        "spacing": "md",
        "margin": "lg",
        "contents": buttons,
    }

    return {
        "type": "flex",
        "altText": "CARE 官方入口",
        "contents": {
            "type": "bubble",
            "size": "giga",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "xl",
                "contents": [
                    {
                        "type": "text",
                        "text": "CARE 官方入口",
                        "weight": "bold",
                        "wrap": True,
                        "size": "4xl",
                        "color": "#111111",
                    },
                    {
                        "type": "text",
                        "wrap": True,
                        "text": "請選擇以下方式開啟 CARE 官方入口。",
                        "color": "#333333",
                        "size": "24px",
                    },
                    {
                        "type": "separator",
                        "margin": "md",
                        "color": "#CCCCCC",
                    },
                    button_layout,
                ],
            },
        },
    }
