"""每日醫療消息卡的三張版面。

三張刻意不共用同一個 builder：

- **Tier 1**（命中使用者用藥的警訊）與 **Tier 2**（保底衛教）必須在版面上明顯
  不同。這是 design.md 決策 1 的承重條件——每日必推的代價是使用者會學會忽略這張
  卡，若兩層長得一樣，Tier 1 的高價值內容會被 Tier 2 一起稀釋掉，而稀釋的過程
  沒有任何訊號（不報錯、不留 log，只表現為「使用者不再點卡片」）。
- **分享卡**是零洩漏的：它的介面上根本沒有藥名參數，因此不存在「呼叫端不小心
  把藥名傳進來」的路徑。摘要本身已由 grader 寫成中性第三人稱，分享時只要不帶
  Tier 1 的標題列與藥名列即可，不需要再做一次文字改寫。

三張都不接受 `indication`／`spc_indication`／`spc_indication_summary`——
`app/models/medication.py` 對這三個欄位有「SHALL NOT 進入任何推播訊息」的明文
禁令，理由是適應症直接揭露病情。以簽章排除比以字串過濾可靠。
"""

from __future__ import annotations

from typing import Any

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
from resources.flex_messages import size_guard, theme

# Tier 1 用琥珀色而非紅色。紅色在既有版面裡代表「休診／關閉」，且對高齡使用者
# 是恐嚇；琥珀色足以與 Tier 2 的品牌綠明顯區隔，又不至於讓人以為出了急事。
TIER1_HEADER_BG = theme.STATUS_PENDING
TIER2_HEADER_BG = theme.BRAND
SHARED_HEADER_BG = theme.BRAND_DARK

# 卡片上摘要與標題的字數上限。先在這裡收斂，`_fit` 的逐步截斷才只會在異常長的
# 內容上啟動，而不是每張卡都要跑一輪。
_TITLE_CHARS = 60
_SUMMARY_CHARS = 200

# `_fit` 每一輪把摘要砍掉的比例。
_SHRINK_RATIO = 0.6
_MIN_SUMMARY_CHARS = 20


def _header(label: str, ft: theme.FlexTheme, background: str) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": background,
        "paddingAll": "lg",
        "contents": [
            {
                "type": "text",
                "text": label,
                "color": theme.TEXT_ON_BRAND,
                "weight": "bold",
                "size": ft.heading,
                "wrap": True,
            }
        ],
    }


def _title(text: str, ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "text",
        "text": text[:_TITLE_CHARS],
        "size": ft.body,
        "color": theme.TEXT,
        "weight": "bold",
        "wrap": True,
    }


def _paragraph(text: str, ft: theme.FlexTheme, color: str = theme.TEXT_MUTED, **extra):
    return {
        "type": "text",
        "text": text,
        "size": ft.body,
        "color": color,
        "wrap": True,
        **extra,
    }


def _caption(text: str, ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "text",
        "text": text,
        "size": ft.caption,
        "color": theme.TEXT_FAINT,
        "wrap": True,
    }


def _body(contents: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "xl",
        "backgroundColor": theme.SURFACE,
        "spacing": "md",
        "contents": contents,
    }


def _footer(buttons: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "lg",
        "spacing": "sm",
        "contents": buttons,
    }


def _source_button(url: str, ft: theme.FlexTheme, language: str | None):
    label = t("news.source_button", language)
    return ft.secondary_button(label, {"type": "uri", "label": label, "uri": url})


def _share_button(news_ref: str, ft: theme.FlexTheme, language: str | None):
    label = t("news.share_button", language)
    return ft.primary_button(
        label,
        {
            "type": "postback",
            "label": label,
            "data": f"action=share_medical_news&news_ref={news_ref}",
            "displayText": t("news.share_display", language),
        },
    )


def _fit(bubble: dict[str, Any], summary_node: dict[str, Any]) -> dict[str, Any]:
    """把 bubble 收進 LINE 的大小上限內。

    超過時逐步截短摘要而不是直接放棄：摘要是唯一長度不可控的部分（標題與按鈕
    都有上限），而一張少了半句摘要的卡片仍然有用——使用者還有標題與來源連結。
    真的縮不下去才拋，由呼叫端退回純文字。
    """
    if size_guard.fits(bubble):
        return bubble

    text = summary_node["text"]
    while len(text) > _MIN_SUMMARY_CHARS:
        text = text[: int(len(text) * _SHRINK_RATIO)]
        summary_node["text"] = text + "…"
        if size_guard.fits(bubble):
            return bubble

    raise ValueError("medical news bubble exceeds LINE size limit")


def build_tier1_news_bubble(
    *,
    news_ref: str,
    drug_name: str,
    title: str,
    summary: str,
    source_name: str,
    url: str,
    language: str | None = None,
    font_size: str | None = None,
) -> dict[str, Any]:
    ft = theme.resolve_theme(font_size)
    summary_node = _paragraph(summary[:_SUMMARY_CHARS], ft)
    bubble = {
        "type": "bubble",
        "header": _header(t("news.tier1_header", language), ft, TIER1_HEADER_BG),
        "body": _body(
            [
                _paragraph(
                    t("news.drug_label", language).format(name=drug_name),
                    ft,
                    color=theme.TEXT,
                    weight="bold",
                ),
                _title(title, ft),
                summary_node,
                _caption(source_name, ft),
                # 固定行動呼籲。常數文案，不由模型產生——這是主動推播裡唯一
                # 允許出現的「該怎麼做」，其餘一律由輸出防線擋掉。
                _paragraph(
                    t("news.consult_professional", language), ft, margin="md"
                ),
            ]
        ),
        "footer": _footer(
            [_source_button(url, ft, language), _share_button(news_ref, ft, language)]
        ),
    }
    return _fit(bubble, summary_node)


def build_tier2_news_bubble(
    *,
    news_ref: str,
    title: str,
    summary: str,
    source_name: str,
    url: str,
    language: str | None = None,
    font_size: str | None = None,
) -> dict[str, Any]:
    """保底衛教卡。**介面上沒有藥名參數**——Tier 2 與使用者的用藥無關。

    也刻意沒有「請與您的醫師或藥師確認」那一行：這則消息不涉及使用者正在服用的
    任何藥，掛上「不要自行改變用藥」是無關的恐嚇。
    """
    ft = theme.resolve_theme(font_size)
    summary_node = _paragraph(summary[:_SUMMARY_CHARS], ft)
    bubble = {
        "type": "bubble",
        "header": _header(t("news.tier2_header", language), ft, TIER2_HEADER_BG),
        "body": _body([_title(title, ft), summary_node, _caption(source_name, ft)]),
        "footer": _footer(
            [_source_button(url, ft, language), _share_button(news_ref, ft, language)]
        ),
    }
    return _fit(bubble, summary_node)


def build_shared_news_bubble(
    *,
    sharer_name: str,
    title: str,
    summary: str,
    source_name: str,
    url: str,
    language: str | None = None,
    font_size: str | None = None,
) -> dict[str, Any]:
    """家人分享過來的消息卡。

    **沒有分享按鈕**：再掛一顆就變成無限轉傳，一則消息會在族譜裡自我繁殖。
    **沒有藥名參數**：收件人不該從這張卡得知分享者在吃什麼藥。
    """
    ft = theme.resolve_theme(font_size)
    summary_node = _paragraph(summary[:_SUMMARY_CHARS], ft)
    bubble = {
        "type": "bubble",
        "header": _header(
            t("news.shared_header", language).format(name=sharer_name),
            ft,
            SHARED_HEADER_BG,
        ),
        "body": _body([_title(title, ft), summary_node, _caption(source_name, ft)]),
        "footer": _footer([_source_button(url, ft, language)]),
    }
    return _fit(bubble, summary_node)


def build_tier1_news_flex(**kwargs) -> FlexMessage:
    language = kwargs.get("language")
    bubble = build_tier1_news_bubble(**kwargs)
    return FlexMessage(
        altText=t("news.alt_tier1", language),
        contents=FlexContainer.from_dict(bubble),
    )


def build_tier2_news_flex(**kwargs) -> FlexMessage:
    language = kwargs.get("language")
    bubble = build_tier2_news_bubble(**kwargs)
    return FlexMessage(
        altText=t("news.alt_tier2", language),
        contents=FlexContainer.from_dict(bubble),
    )


def build_shared_news_flex(**kwargs) -> FlexMessage:
    language = kwargs.get("language")
    bubble = build_shared_news_bubble(**kwargs)
    return FlexMessage(
        altText=t("news.alt_shared", language),
        contents=FlexContainer.from_dict(bubble),
    )
