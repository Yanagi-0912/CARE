"""
緊急狀況家人通報卡：對話中判定為緊急時，推播給合格家屬的 Flex Message。
卡片含當事人的原話，而且是逐字：

為什麼免責寫得比其他卡片重：
    這是唯一一種「系統判斷錯誤會驚動第三人」的通知。誤報收不回來，所以卡片
    必須明說它可能是錯的，並且把「以你實際聯繫到的情況為準」放在最後一行。

字級與語言取收件人自己的設定：背景推播沒有 request context，由呼叫端逐一
查出來後傳入（見 emergency_alert_service._display_prefs）。
"""

from __future__ import annotations

from typing import Any, Optional

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n.messages import t
from resources.flex_messages import theme

# --- 樣式常數。改這裡等同改模板，兩邊必須同步（有測試比對）-------------------
#
# 底色刻意與當事人那張紅卡（#C62828）不同：家屬看到的是「需要你去確認」，
# 不是「你正處於危險中」。同樣是警示色系但降一階，避免在家族群組裡造成
# 過度反應——而過度反應會讓下一次真的緊急時被當成又一次誤報。
_TPL_HEADER_BG = "#B3261E"
_TPL_BUTTON_BG = "#8C1D18"
_TPL_ON_DARK = "#FFFFFF"
_TPL_ON_DARK_MUTED = "#F2DEDC"
_TPL_REASON_BG = "#FBEEEC"
_TPL_REASON_BORDER = "#E7C9C5"
# 原話用中性灰底而非警示色：它是引述，不是系統的判斷。視覺上要讓家屬一眼分得出
# 「這是他本人說的」與「這是系統推論的」——混在一起會讓誤判看起來像事實。
_TPL_QUOTE_BG = "#F5F5F5"
_TPL_QUOTE_BORDER = "#DDDDDD"
_TPL_QUOTE_BAR = "#8C1D18"

# 原話的字數上限。超過就截斷——卡片放不下整段病史，而急救需要的資訊幾乎一定
# 在開頭。截斷優於不顯示，也優於把卡片撐爆讓 LINE 整則退回。
MAX_QUOTED_CHARS = 150
_TPL_LABEL_COLOR = "#555555"
_TPL_STEP_COLOR = "#333333"
_TPL_SEPARATOR_COLOR = "#E0E0E0"


def alt_text(patient_name: str, language: str | None = None) -> str:
    """LINE 通知列上唯一看得到的字。名字要在裡面，家屬才知道是誰。"""
    return t("emergency_family.alt_text", language).format(name=patient_name)


def _text(
    value: str,
    *,
    size: str,
    color: str = theme.TEXT,
    weight: str | None = None,
    margin: str | None = None,
    align: str | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "text",
        "text": value,
        "size": size,
        "color": color,
        "wrap": True,
    }
    if weight:
        node["weight"] = weight
    if margin:
        node["margin"] = margin
    if align:
        node["align"] = align
    return node


def _header(patient_name: str, ft: theme.FlexTheme, language: str | None) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": _TPL_HEADER_BG,
        "paddingAll": "20px",
        "contents": [
            _text(
                t("emergency_family.title", language),
                size=ft.caption,
                color=_TPL_ON_DARK_MUTED,
                weight="bold",
            ),
            _text(
                patient_name,
                size=ft.title,
                color=_TPL_ON_DARK,
                weight="bold",
                margin="sm",
            ),
        ],
    }


def _quote_box(
    words: str, patient_name: str, ft: theme.FlexTheme, language: str | None
) -> dict[str, Any]:
    """當事人的原話，逐字。左側色條是引述的視覺記號。"""
    quoted = words.strip()
    if len(quoted) > MAX_QUOTED_CHARS:
        quoted = quoted[:MAX_QUOTED_CHARS] + "…"
    return {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "backgroundColor": _TPL_QUOTE_BG,
        "borderColor": _TPL_QUOTE_BORDER,
        "borderWidth": "1px",
        "cornerRadius": "8px",
        "paddingAll": "16px",
        "margin": "md",
        "contents": [
            _text(
                t("emergency_family.words_label", language).format(name=patient_name),
                size=ft.caption,
                color=_TPL_LABEL_COLOR,
                weight="bold",
            ),
            {
                "type": "box",
                "layout": "horizontal",
                "margin": "sm",
                "contents": [
                    # 左側色條。用 filler + 背景色畫，Flex 沒有 border-left。
                    {
                        "type": "box",
                        "layout": "vertical",
                        "width": "4px",
                        "backgroundColor": _TPL_QUOTE_BAR,
                        "cornerRadius": "2px",
                        "contents": [{"type": "filler"}],
                        "flex": 0,
                    },
                    {
                        "type": "text",
                        "text": quoted,
                        "size": ft.body,
                        "color": theme.TEXT,
                        "wrap": True,
                        "margin": "md",
                        "weight": "bold",
                    },
                ],
            },
        ],
    }


def _reason_box(reason: str, ft: theme.FlexTheme, language: str | None) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "backgroundColor": _TPL_REASON_BG,
        "borderColor": _TPL_REASON_BORDER,
        "borderWidth": "1px",
        "cornerRadius": "8px",
        "paddingAll": "16px",
        "margin": "md",
        "contents": [
            _text(
                t("emergency_family.reason_label", language),
                size=ft.caption,
                color=_TPL_LABEL_COLOR,
                weight="bold",
            ),
            _text(reason, size=ft.body, color=theme.TEXT, margin="xs"),
        ],
    }


def _step(index: int, value: str, ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "baseline",
        "margin": "md",
        "contents": [
            {
                "type": "text",
                "text": f"{index}.",
                "size": ft.body,
                "color": _TPL_BUTTON_BG,
                "weight": "bold",
                "flex": 0,
            },
            {
                "type": "text",
                "text": value,
                "size": ft.body,
                "color": _TPL_STEP_COLOR,
                "wrap": True,
                "margin": "sm",
            },
        ],
    }


def _call_button(
    label: str, tel_uri: str, ft: theme.FlexTheme, *, primary: bool
) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": _TPL_BUTTON_BG if primary else theme.SURFACE,
        "borderColor": _TPL_BUTTON_BG,
        "borderWidth": "1px",
        "cornerRadius": "md",
        "paddingAll": "lg",
        "margin": "md",
        "action": {"type": "uri", "label": label, "uri": tel_uri},
        "contents": [
            _text(
                label,
                size=ft.button,
                weight="bold",
                color=_TPL_ON_DARK if primary else _TPL_BUTTON_BG,
                align="center",
            )
        ],
    }


def build_emergency_family_bubble(
    *,
    patient_name: str,
    reason: str,
    patient_words: str = "",
    patient_tel_uri: Optional[str] = None,
    language: str | None = None,
    font_size: str | None = None,
) -> dict[str, Any]:
    """組出 bubble 的 raw dict。供模板產生與大小檢查用。

    patient_words 是當事人的原話，逐字帶入不改寫。空字串時整段不出現——
    取不到原話（例如語音或圖片訊息）不該讓卡片留一個空引述框。

    patient_tel_uri 省略時不顯示撥號按鈕——CARE 沒有存使用者電話，多數情況下
    會是 None。缺按鈕不影響卡片可用性：步驟說明仍然告訴家屬先打電話，家屬本來
    就有當事人的號碼。有值時才多給一個一鍵撥號的捷徑。
    """
    ft = theme.resolve_theme(font_size)
    name = patient_name or t("emergency_family.fallback_name", language)

    body_contents: list[dict[str, Any]] = [
        _text(
            t("emergency_family.lead", language).format(name=name),
            size=ft.body,
        ),
    ]
    # 原話排在系統判定之前：它是事實，判定是推論。家屬掃過卡片時最先看到的
    # 應該是「他說了什麼」。
    if patient_words and patient_words.strip():
        body_contents.append(_quote_box(patient_words, name, ft, language))
    body_contents += [
        _reason_box(reason, ft, language),
        {"type": "separator", "margin": "xl", "color": _TPL_SEPARATOR_COLOR},
        _text(
            t("emergency_family.action_label", language),
            size=ft.caption,
            color=_TPL_LABEL_COLOR,
            weight="bold",
            margin="xl",
        ),
        _step(1, t("emergency_family.step.1", language).format(name=name), ft),
        _step(2, t("emergency_family.step.2", language), ft),
    ]

    if patient_tel_uri:
        body_contents.append(
            _call_button(
                t("emergency_family.call_patient", language).format(name=name),
                patient_tel_uri,
                ft,
                primary=True,
            )
        )

    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": _header(name, ft, language),
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "20px",
            "spacing": "md",
            "backgroundColor": theme.SURFACE,
            "contents": body_contents,
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "backgroundColor": theme.SURFACE_ALT,
            "contents": [
                _text(
                    t("emergency_family.footer", language),
                    size=ft.caption,
                    color=theme.TEXT_FAINT,
                )
            ],
        },
    }

    return bubble


def build_emergency_family_flex(
    *,
    patient_name: str,
    reason: str,
    patient_words: str = "",
    patient_tel_uri: Optional[str] = None,
    language: str | None = None,
    font_size: str | None = None,
) -> FlexMessage:
    """組出可直接交給 LineReplier.push_flex 的 SDK FlexMessage。

    altText 刻意不含原話：通知列會出現在鎖定畫面上。
    """
    name = patient_name or t("emergency_family.fallback_name", language)
    bubble = build_emergency_family_bubble(
        patient_name=name,
        reason=reason,
        patient_words=patient_words,
        patient_tel_uri=patient_tel_uri,
        language=language,
        font_size=font_size,
    )
    return FlexMessage(
        altText=alt_text(name, language),
        contents=FlexContainer.from_dict(bubble),
    )
