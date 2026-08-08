"""官網／LIFF 入口 Flex Message 產生器。"""

from typing import Any

from app.i18n import t
from resources.flex_messages import theme


def generate_official_site_flex_message(
    liff_url: str,
    public_base_url: str,
    language: str | None = None,
    font_size: str | None = None,
) -> dict[str, Any]:
    """
    產生官網入口 Flex Message，只呈現單一入口按鈕。

    LIFF 與官網對使用者而言是同一個目的地，因此優先採用 LIFF；
    未設定 LIFF 時才退回官網 URL。
    language / font_size 為 None 時沿用 request-scoped 設定。
    """
    entry_url = liff_url.strip() or public_base_url.strip()
    if not entry_url:
        raise ValueError("At least one non-empty URL is required")

    ft = theme.resolve_theme(font_size)
    button_label = t("flex.site.button", language)

    return {
        "type": "flex",
        "altText": t("flex.site.alt", language),
        "contents": {
            "type": "bubble",
            "size": "giga",
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "xl",
                "backgroundColor": theme.SURFACE,
                "contents": [
                    {
                        "type": "text",
                        "text": "CARE",
                        "weight": "bold",
                        "size": ft.caption,
                        "color": theme.BRAND,
                    },
                    {
                        "type": "text",
                        "text": t("flex.site.title", language),
                        "weight": "bold",
                        "wrap": True,
                        "size": ft.title,
                        "color": theme.TEXT,
                        "margin": "xs",
                    },
                    {
                        "type": "text",
                        "text": t("flex.site.desc", language),
                        "wrap": True,
                        "size": ft.body,
                        "color": theme.TEXT_MUTED,
                        "margin": "md",
                    },
                    theme.divider("xl"),
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "xl",
                        "contents": [
                            ft.primary_button(
                                button_label,
                                {
                                    "type": "uri",
                                    "label": button_label,
                                    "uri": entry_url,
                                },
                            )
                        ],
                    },
                ],
            },
        },
    }
