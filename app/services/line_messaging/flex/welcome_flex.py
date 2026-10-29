"""加好友時的歡迎卡，也是傳「功能說明」叫出來的那一份（見 feature_guide_intent）。

收件人剛加入好友，多半還沒開過 LIFF——資料庫裡沒有 profile，也不知道 CARE 能
做什麼。卡片是一則輪播：第一頁歡迎與填健康資料，後面每頁一類功能（分類見
SECTIONS），每項寫在 LINE 裡怎麼說才會觸發。加好友只會收到這一次，所以第一頁
告訴使用者之後怎麼再叫出來。

「試試看」按鈕是 message action，按下去等於使用者自己打了那句話，走一般文字訊息
的處理流程，不需要額外的 postback 分支。按鈕只放沒有副作用、新好友也答得出來的
句子：「我迷路了」會通知家人、「早上那頓我吃過了」會記服藥、回報知識問題會送單，
這些只寫在說明裡；查服藥紀錄、家人名單、上傳文件，對還沒有資料的新好友只會得到
「查不到」，也不放。血壓與紅豆兩題取自 evals/rag/golden.jsonl：「血壓多少算高」
是 kb-004；紅豆那題是 verdict-003，預期判定「錯誤」，會出判定卡。沒命中查核資料的
謠言只會得到「證據不足」，拿來示範反而讓人以為查核沒用。

卡片內的 action 一律不帶 label：label 只在 button 元件上會顯示，這裡的按鈕是 box；
SDK 的 MessageAction／URIAction 也把 label 標為選填。範例句譯成泰文、印尼文後都
不短，不帶 label 就不必擔心長度驗證。快速回覆的相機按鈕是例外，LINE 規定快速
回覆一定要有 label。相機與相簿只能放在快速回覆，Flex 裡沒有這兩種 action。
"""

from typing import Any, NamedTuple

from linebot.v3.messaging import (
    CameraAction,
    CameraRollAction,
    FlexContainer,
    FlexMessage,
    QuickReply,
    QuickReplyItem,
)

from app.i18n import t
from app.services.line_messaging.quick_reply import with_feature_guide
from app.services.line_messaging.rich_menu_layout import liff_uri
from resources.flex_messages import theme


class FeatureSection(NamedTuple):
    """輪播的一頁。文字都是 i18n key 的一段，見 messages.py 的 welcome.section／feature。"""

    key: str
    features: tuple[str, ...]
    # 「試試看」按鈕送出的句子（i18n key）。
    examples: tuple[str, ...] = ()


# 順序即輪播順序。
SECTIONS: tuple[FeatureSection, ...] = (
    FeatureSection(
        "ai",
        ("health_qa", "rumor"),
        ("welcome.example.health", "welcome.example.rumor"),
    ),
    FeatureSection("media", ("drug_bag", "clinic_recording")),
    FeatureSection("daily", ("documents", "med_status", "med_report")),
    FeatureSection(
        "resources",
        ("department", "nearby", "facility", "my_meds"),
        ("welcome.example.department", "welcome.example.nearby"),
    ),
    FeatureSection("family", ("invite", "directory", "lost"), ("welcome.example.invite",)),
    FeatureSection(
        "other",
        ("kb_report", "official_site", "risk"),
        ("welcome.example.official_site",),
    ),
)

EXAMPLE_KEYS: tuple[str, ...] = tuple(key for section in SECTIONS for key in section.examples)

# 「看操作示範」按鈕的 postback action（dispatcher 依此回示範影片）。
FEATURE_VIDEO_ACTION = "feature_video"

# 年齡、慢性病等欄位都在這一頁，agent 回答時會帶入（nodes.format_user_profile_prompt）。
PROFILE_PATH = "/personalhealth"


def feature_video_postback(section: str) -> str:
    return f"action={FEATURE_VIDEO_ACTION}&section={section}"


def build_welcome_flex(
    liff_url: str,
    language: str | None = None,
    font_size: str | None = None,
    *,
    with_videos: bool = False,
) -> FlexMessage:
    """歡迎卡。`liff_url` 為空時省略填資料按鈕，其餘照常。

    `with_videos` 為真時每頁功能上方多一顆「看操作示範」。影片網址要用
    PUBLIC_BASE_URL 組（LINE 伺服器來抓），沒設定就不放按鈕，免得按了沒影片。
    """
    ft = theme.resolve_theme(font_size)
    title = t("welcome.title", language)
    carousel = {
        "type": "carousel",
        "contents": [
            _intro_bubble(title, liff_url, language, ft),
            *(_section_bubble(section, language, ft, with_videos) for section in SECTIONS),
        ],
    }
    quick_reply = QuickReply(
        items=[
            QuickReplyItem(action=CameraAction(label=t("welcome.quick.camera", language))),
            QuickReplyItem(
                action=CameraRollAction(label=t("welcome.quick.camera_roll", language))
            ),
        ]
    )
    return FlexMessage(
        altText=title,
        contents=FlexContainer.from_dict(carousel),
        quickReply=with_feature_guide(quick_reply, language),
    )


def _intro_bubble(title: str, liff_url: str, language: str | None, ft: theme.FlexTheme) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        _text(t("welcome.lead", language), ft.body, theme.TEXT),
        {**_text(t("welcome.swipe_hint", language), ft.body, theme.BRAND), "weight": "bold"},
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
    body += [
        _text(t("welcome.recall_hint", language), ft.caption, theme.TEXT_MUTED),
        _text(t("welcome.disclaimer", language), ft.caption, theme.TEXT_FAINT),
    ]
    return _bubble(title, body, ft)


def _section_bubble(
    section: FeatureSection, language: str | None, ft: theme.FlexTheme, with_videos: bool
) -> dict[str, Any]:
    body: list[dict[str, Any]] = []
    if with_videos:
        body.append(
            ft.primary_button(
                t("welcome.video_button", language),
                {
                    "type": "postback",
                    "data": feature_video_postback(section.key),
                    "displayText": t("welcome.video_display", language),
                },
            )
        )
    body += [
        {
            "type": "box",
            "layout": "vertical",
            "spacing": "xs",
            "contents": [
                {
                    **_text(
                        t(f"welcome.feature.{name}.name", language), ft.body, theme.TEXT
                    ),
                    "weight": "bold",
                },
                _text(t(f"welcome.feature.{name}.how", language), ft.caption, theme.TEXT_MUTED),
            ],
        }
        for name in section.features
    ]
    if section.examples:
        body += [
            theme.divider(),
            _text(t("welcome.try_hint", language), ft.caption, theme.TEXT_MUTED),
            *(
                ft.secondary_button(text, {"type": "message", "text": text})
                for text in (t(key, language) for key in section.examples)
            ),
        ]
    return _bubble(t(f"welcome.section.{section.key}.title", language), body, ft)


def _bubble(title: str, body: list[dict[str, Any]], ft: theme.FlexTheme) -> dict[str, Any]:
    return {
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
            "spacing": "lg",
            "contents": body,
        },
    }


def _text(text: str, size: str, color: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "size": size, "color": color, "wrap": True}
