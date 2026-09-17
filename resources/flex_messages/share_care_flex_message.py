"""分享 CARE 的 Flex Message 產生器：官方帳號 QR、分享按鈕、邀請家人按鈕。

網址格式照 LINE URL scheme 文件
（https://developers.line.biz/en/docs/messaging-api/using-line-url-scheme/）：
加好友頁與「分享給好友」畫面都要把 LINE ID 的 @ 編成 %40。文件另外註明
recommendOA 用 %40 的寫法在 Android 版 LINE 13.8.0 以前打不開，這裡照文件
現行的寫法。

QR 圖直接用 LINE 提供的圖——官方帳號後台「加入好友」QR 的嵌入碼就是這個網址，
不必自己產圖。它編的內容就是加好友連結（2026-09-15 解碼核對過）。

按鈕的 action 不帶 label：LINE 對 label 有字數上限，英文的「邀請家人」一定
超過，超過整則回覆會被拒收。按鈕上的字由 theme 的按鈕框自己畫。
"""

from typing import Any
from urllib.parse import quote

from app.i18n import t
from app.services.line_messaging.rich_menu_layout import liff_uri
from resources.flex_messages import theme

FAMILY_PATH = "/family"


def _line_id(basic_id: str) -> str:
    """統一成帶 @ 的 LINE ID（get_bot_info 的 basicId 本來就帶）。"""
    handle = basic_id.strip()
    return handle if handle.startswith("@") else f"@{handle}"


def generate_share_care_flex_message(
    basic_id: str,
    liff_url: str,
    language: str | None = None,
    font_size: str | None = None,
) -> dict[str, Any]:
    """產生分享卡。

    回傳值多一個頂層鍵 `followUpText`：卡片後面要再送一則純文字的加好友連結
    ——卡片裡的字不能長按複製，純文字才能複製、轉貼到別的地方。reply.py 會把
    它拆成第二則訊息，不會進到送給 LINE 的 FlexMessage。

    `liff_url` 為空時省略邀請家人那一段。language / font_size 為 None 時沿用
    request-scoped 設定。
    """
    line_id = _line_id(basic_id)
    encoded_id = quote(line_id, safe="")
    add_friend_url = f"https://line.me/R/ti/p/{encoded_id}"
    recommend_url = f"https://line.me/R/nv/recommendOA/{encoded_id}"
    qr_image_url = f"https://qr-official.line.me/sid/L/{line_id[1:]}.png"

    ft = theme.resolve_theme(font_size)
    title = t("flex.share.title", language)

    body: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "CARE",
            "weight": "bold",
            "size": ft.caption,
            "color": theme.BRAND,
        },
        {
            "type": "text",
            "text": title,
            "weight": "bold",
            "wrap": True,
            "size": ft.title,
            "color": theme.TEXT,
            "margin": "xs",
        },
        {
            "type": "text",
            "text": t("flex.share.desc", language),
            "wrap": True,
            "size": ft.body,
            "color": theme.TEXT_MUTED,
            "margin": "md",
        },
        {
            "type": "image",
            "url": qr_image_url,
            "size": "full",
            "aspectRatio": "1:1",
            "aspectMode": "fit",
            "margin": "lg",
        },
        {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "contents": [
                ft.primary_button(
                    t("flex.share.button", language),
                    {"type": "uri", "uri": recommend_url},
                )
            ],
        },
    ]

    if liff_url.strip():
        body.extend(
            [
                theme.divider("xl"),
                {
                    "type": "text",
                    "text": t("flex.share.family_prompt", language),
                    "wrap": True,
                    "size": ft.body,
                    "color": theme.TEXT_MUTED,
                    "margin": "xl",
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "md",
                    "contents": [
                        ft.secondary_button(
                            t("flex.share.family_button", language),
                            {"type": "uri", "uri": liff_uri(liff_url, FAMILY_PATH)},
                        )
                    ],
                },
            ]
        )

    return {
        "type": "flex",
        "altText": title,
        "contents": {
            "type": "bubble",
            "size": "giga",
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "xl",
                "backgroundColor": theme.SURFACE,
                "contents": body,
            },
        },
        "followUpText": t("share.link_text", language).format(url=add_friend_url),
    }
