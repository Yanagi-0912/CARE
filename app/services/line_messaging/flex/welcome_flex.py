"""加好友時的歡迎卡。

收件人剛加入好友，多半還沒開過 LIFF——資料庫裡沒有 profile，也不知道 CARE 能
做什麼。卡片只做兩件事：示範幾句能直接問的話，以及引導去 LIFF 填健康資料。

範例按鈕是 message action，按下去等於使用者自己打了那句話，走一般文字訊息的
處理流程，不需要額外的 postback 分支。範例只挑線上確實答得好的題目，取自
evals/rag/golden.jsonl：「血壓多少算高」是 kb-004；紅豆那題是 verdict-003，預期
判定「錯誤」，會出判定卡。沒命中查核資料的謠言只會得到「證據不足」，拿來示範
反而讓人以為查核沒用。語音輸入仰賴的 ASR 目前沒有部署，因此不列。

action 一律不帶 label：label 只在 button 元件上會顯示，這裡的按鈕是 box；SDK 的
MessageAction／URIAction 也把 label 標為選填。範例句譯成泰文、印尼文後都不短，
不帶 label 就不必擔心長度驗證。
"""

from typing import Any

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
from app.services.line_messaging.rich_menu_layout import liff_uri
from resources.flex_messages import theme

# 卡片上的範例問句，順序即顯示順序。
EXAMPLE_KEYS: tuple[str, ...] = (
    "welcome.example.health",
    "welcome.example.rumor",
    "welcome.example.nearby",
)

# 年齡、慢性病等欄位都在這一頁，agent 回答時會帶入（nodes.format_user_profile_prompt）。
PROFILE_PATH = "/personalhealth"


def build_welcome_flex(
    liff_url: str,
    language: str | None = None,
    font_size: str | None = None,
) -> FlexMessage:
    """歡迎卡。`liff_url` 為空時省略填資料按鈕，其餘照常。"""
    ft = theme.resolve_theme(font_size)
    title = t("welcome.title", language)

    body: list[dict[str, Any]] = [
        _text(t("welcome.lead", language), ft.body, theme.TEXT),
        {
            "type": "box",
            "layout": "vertical",
            "spacing": "xs",
            "margin": "lg",
            "contents": [
                ft.section_title(t("welcome.examples_title", language)),
                _text(t("welcome.examples_hint", language), ft.caption, theme.TEXT_MUTED),
            ],
        },
        *(
            ft.secondary_button(text, {"type": "message", "text": text})
            for text in (t(key, language) for key in EXAMPLE_KEYS)
        ),
        _text(t("welcome.photo_hint", language), ft.body, theme.TEXT_MUTED),
        theme.divider(),
    ]

    if liff_url:
        body += [
            ft.primary_button(
                t("welcome.profile_button", language),
                {"type": "uri", "uri": liff_uri(liff_url, PROFILE_PATH)},
            ),
            _text(t("welcome.profile_hint", language), ft.caption, theme.TEXT_MUTED),
        ]

    body.append(_text(t("welcome.disclaimer", language), ft.caption, theme.TEXT_FAINT))

    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": theme.BRAND,
            "paddingAll": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": title,
                    "color": theme.TEXT_ON_BRAND,
                    "weight": "bold",
                    "size": ft.heading,
                    "wrap": True,
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "xl",
            "backgroundColor": theme.SURFACE,
            "spacing": "md",
            "contents": body,
        },
    }

    return FlexMessage(altText=title, contents=FlexContainer.from_dict(bubble))


def _text(text: str, size: str, color: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "size": size, "color": color, "wrap": True}
