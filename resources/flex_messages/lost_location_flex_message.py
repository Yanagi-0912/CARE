"""走失求救的 Flex 卡片：長輩端的「讓家人看到我在哪裡」與家人端的通報。

字級與語言：長輩那張是回覆當下組的，讀 request 的設定；推給家人的由呼叫端逐一
查出收件人自己的設定傳入（背景推播沒有 request context）。

配色：
- 長輩的卡用品牌綠。他正在慌，卡片要像有人來幫忙，不要再用警示色嚇他一次。
- 家人的走失通報用琥珀色（theme.STATUS_PENDING），刻意不用緊急通報的紅：
  走失是「需要你現在去確認、去找」，不是「有人失去意識」。兩種卡片在家人的
  聊天室裡要一眼分得出來。
"""

from __future__ import annotations

from typing import Any, Optional

from linebot.v3.messaging import (
    FlexContainer,
    FlexMessage,
    LocationAction,
    QuickReply,
    QuickReplyItem,
)

from app.i18n.messages import t
from resources.flex_messages import theme

# 原話的字數上限，理由同緊急通報卡：卡片放不下長篇，求救的重點在開頭。
MAX_QUOTED_CHARS = 150

_LOST_ACCENT = theme.STATUS_PENDING
_QUOTE_BG = "#F5F5F5"
_QUOTE_BORDER = "#DDDDDD"


def _text(
    value: str,
    *,
    size: str,
    color: str = theme.TEXT,
    weight: Optional[str] = None,
    margin: Optional[str] = None,
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
    return node


def _header(title: str, background: str, ft: theme.FlexTheme, caption: str = "") -> dict[str, Any]:
    contents: list[dict[str, Any]] = []
    if caption:
        contents.append(
            _text(caption, size=ft.caption, color=theme.TEXT_ON_BRAND, weight="bold")
        )
    contents.append(
        _text(
            title,
            size=ft.heading,
            color=theme.TEXT_ON_BRAND,
            weight="bold",
            margin="sm" if caption else None,
        )
    )
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": background,
        "paddingAll": "20px",
        "contents": contents,
    }


def _bubble(header: dict[str, Any], body: list[dict[str, Any]], footer: Optional[str], ft: theme.FlexTheme) -> dict[str, Any]:
    bubble: dict[str, Any] = {
        "type": "bubble",
        "size": "mega",
        "header": header,
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "20px",
            "spacing": "md",
            "backgroundColor": theme.SURFACE,
            "contents": body,
        },
    }
    if footer:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "backgroundColor": theme.SURFACE_ALT,
            "contents": [_text(footer, size=ft.caption, color=theme.TEXT_FAINT)],
        }
    return bubble


def _uri(label: str, uri: str) -> dict[str, Any]:
    # action 的 label 上限 20 字元，超過 LINE 會整則退回；畫面上的字另外由
    # 按鈕裡的 text 節點呈現，不受這個上限影響。
    return {"type": "uri", "label": label[:20], "uri": uri}


def _location_quick_reply(language: Optional[str]) -> QuickReply:
    """聊天室下方的「傳送一次位置」。LIFF 打不開（沒給定位權限、舊版 LINE）時的退路。"""
    return QuickReply(
        items=[
            QuickReplyItem(
                action=LocationAction(label=t("lost.elder.quick_reply", language))
            )
        ]
    )


# ── 長輩端 ───────────────────────────────────────────────────────────


def build_elder_share_bubble(
    *,
    header_key: str,
    share_url: Optional[str],
    body_key: str = "lost.elder.body",
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> dict[str, Any]:
    """一顆大按鈕打開定位頁。

    share_url 為 None（沒設 LIFF_ID）時不放按鈕，只留「傳送一次位置」的說明——
    放一顆打不開的按鈕，比沒有按鈕更讓人慌。
    """
    ft = theme.resolve_theme(font_size)
    body: list[dict[str, Any]] = []
    if share_url:
        body += [
            _text(t(body_key, language), size=ft.body),
            {
                **ft.primary_button(
                    t("lost.elder.button", language),
                    _uri(t("lost.elder.button", language), share_url),
                ),
                "margin": "lg",
            },
            _text(t("lost.elder.stay", language), size=ft.body, weight="bold", margin="lg"),
        ]
    body.append(
        _text(
            t("lost.elder.fallback_hint", language),
            size=ft.caption,
            color=theme.TEXT_MUTED,
            margin="md",
        )
    )
    return _bubble(_header(t(header_key, language), theme.BRAND, ft), body, None, ft)


def build_elder_share_flex(
    *,
    header_key: str,
    share_url: Optional[str],
    body_key: str = "lost.elder.body",
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> FlexMessage:
    bubble = build_elder_share_bubble(
        header_key=header_key,
        share_url=share_url,
        body_key=body_key,
        language=language,
        font_size=font_size,
    )
    return FlexMessage(
        altText=t("lost.elder.alt_text", language),
        contents=FlexContainer.from_dict(bubble),
        quickReply=_location_quick_reply(language),
    )


def build_no_family_flex(
    *, language: Optional[str] = None, font_size: Optional[str] = None
) -> FlexMessage:
    """沒有家人可通知：叫他找人幫忙、一鍵撥 110。"""
    ft = theme.resolve_theme(font_size)
    call_label = t("lost.elder.call_110", language)
    body = [
        _text(t("lost.elder.no_family.body", language), size=ft.body),
        {**ft.primary_button(call_label, _uri(call_label, "tel:110")), "margin": "lg"},
    ]
    bubble = _bubble(
        _header(t("lost.elder.no_family.title", language), _LOST_ACCENT, ft),
        body,
        None,
        ft,
    )
    return FlexMessage(
        altText=t("lost.elder.no_family.title", language),
        contents=FlexContainer.from_dict(bubble),
    )


# ── 家人端 ───────────────────────────────────────────────────────────


def _quote_box(words: str, patient_name: str, ft: theme.FlexTheme, language: Optional[str]) -> dict[str, Any]:
    quoted = words.strip()
    if len(quoted) > MAX_QUOTED_CHARS:
        quoted = quoted[:MAX_QUOTED_CHARS] + "…"
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": _QUOTE_BG,
        "borderColor": _QUOTE_BORDER,
        "borderWidth": "1px",
        "cornerRadius": "8px",
        "paddingAll": "16px",
        "margin": "md",
        "contents": [
            _text(
                t("lost.family.words_label", language).format(name=patient_name),
                size=ft.caption,
                color=theme.TEXT_MUTED,
                weight="bold",
            ),
            _text(quoted, size=ft.body, weight="bold", margin="sm"),
        ],
    }


def family_alert_alt_text(intent: str, patient_name: str, language: Optional[str]) -> str:
    """通知列上唯一看得到的字：誰、發生什麼事。刻意不含原話（會出現在鎖定畫面）。"""
    return t(f"lost.family.alt_text.{intent}", language).format(name=patient_name)


def build_family_alert_bubble(
    *,
    intent: str,
    patient_name: str,
    patient_words: str,
    watch_url: Optional[str],
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> dict[str, Any]:
    """第一張通報：誰、說了什麼、正在等位置、看地圖的按鈕。

    intent 是 "lost" 或 "share"（見 lost_intent）。原話排在說明之後、按鈕之前：
    家人要先知道「他怎麼說」才判斷得出有多急。
    """
    ft = theme.resolve_theme(font_size)
    accent = _LOST_ACCENT if intent == "lost" else theme.BRAND
    body: list[dict[str, Any]] = [
        _text(t(f"lost.family.lead.{intent}", language).format(name=patient_name), size=ft.body),
    ]
    if patient_words and patient_words.strip():
        body.append(_quote_box(patient_words, patient_name, ft, language))
    body.append(
        _text(
            t("lost.family.waiting", language).format(name=patient_name),
            size=ft.body,
            color=theme.TEXT_MUTED,
            margin="md",
        )
    )
    if watch_url:
        label = t("lost.family.view_map", language)
        body.append({**ft.primary_button(label, _uri(label, watch_url)), "margin": "lg"})
    return _bubble(
        _header(patient_name, accent, ft, caption=t(f"lost.family.title.{intent}", language)),
        body,
        t("lost.family.footer", language).format(name=patient_name),
        ft,
    )


def build_family_alert_flex(
    *,
    intent: str,
    patient_name: str,
    patient_words: str,
    watch_url: Optional[str],
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> FlexMessage:
    bubble = build_family_alert_bubble(
        intent=intent,
        patient_name=patient_name,
        patient_words=patient_words,
        watch_url=watch_url,
        language=language,
        font_size=font_size,
    )
    return FlexMessage(
        altText=family_alert_alt_text(intent, patient_name, language),
        contents=FlexContainer.from_dict(bubble),
    )


def build_family_notice_bubble(
    *,
    title: str,
    body_text: str,
    watch_url: Optional[str],
    navigate_url: Optional[str] = None,
    accent: str = theme.BRAND,
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> dict[str, Any]:
    """後續通知（收到位置、位置停止更新）：一句話加上看地圖／導航按鈕。"""
    ft = theme.resolve_theme(font_size)
    body: list[dict[str, Any]] = [_text(body_text, size=ft.body)]
    if watch_url:
        label = t("lost.family.view_map", language)
        body.append({**ft.primary_button(label, _uri(label, watch_url)), "margin": "lg"})
    if navigate_url:
        label = t("lost.family.navigate", language)
        body.append({**ft.secondary_button(label, _uri(label, navigate_url)), "margin": "md"})
    return _bubble(_header(title, accent, ft), body, None, ft)


def build_family_notice_flex(
    *,
    title: str,
    body_text: str,
    watch_url: Optional[str],
    navigate_url: Optional[str] = None,
    accent: str = theme.BRAND,
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> FlexMessage:
    bubble = build_family_notice_bubble(
        title=title,
        body_text=body_text,
        watch_url=watch_url,
        navigate_url=navigate_url,
        accent=accent,
        language=language,
        font_size=font_size,
    )
    return FlexMessage(altText=title, contents=FlexContainer.from_dict(bubble))


LOST_ACCENT = _LOST_ACCENT
