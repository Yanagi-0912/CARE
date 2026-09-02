"""RAG 回答卡：把已經產生好的答案文字組成 LINE Flex Message。

本模組只負責組裝。是否該走卡片、卡片太大要不要退回純文字，都由呼叫端
（reply.py）決定——把降級決策留在呈現層的單一出口，builder 才能保持
「輸入什麼就組出什麼」的單純性質，也才容易測。

字級不自己讀 ContextVar，改由呼叫端傳入 FlexTheme：測試要驗證三種字級的
輸出，傳參數比操作 request-scoped 狀態直接得多。

靜態文字寫死繁中，理由同 verdict_flex.py：卡片主體的答案本文由上游依
使用者語言生成，把「你問的」這幾個字 i18n 而主體是另一種語言，只會生出
半中半外的卡片。若日後要多語系，應與答案生成的語言一起處理。
"""

from __future__ import annotations

from typing import Any, Sequence

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.core.rag_sources import SourceRef
from resources.flex_messages import theme

_HEADER_RAG = "衛教資訊"
_HEADER_DOCUMENT = "文件內容問答"
_QUESTION_LABEL = "你問的"
_SOURCES_LABEL = "參考資料來源"

# LINE altText 官方上限 400 字元，超過會讓整則訊息在送出時被拒絕。
_ALT_TEXT_MAX_LEN = 400

# LINE Flex 的 text 元件不接受空字串，空字串會讓整則訊息在 API 呼叫時直接被
# 拒收（400），使用者什麼都收不到——比顯示一句不完美的預設文字更糟。
# verdict_flex.py 已因同一個原因踩過這個坑。
_BLANK_QUESTION_FALLBACK = "（無法取得原始問句內容）"
_BLANK_BODY_FALLBACK = "（暫無內容，請換個方式再問一次）"


def _header(title: str, ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": theme.BRAND,
        "paddingAll": "lg",
        "contents": [
            {
                "type": "text",
                "text": title,
                "size": ft.heading,
                "color": theme.TEXT_ON_BRAND,
                "weight": "bold",
                "wrap": True,
            }
        ],
    }


def _question_block(question: str, ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": theme.SURFACE_ALT,
        "cornerRadius": "md",
        "paddingAll": "lg",
        "spacing": "xs",
        "contents": [
            {
                "type": "text",
                "text": _QUESTION_LABEL,
                "size": ft.caption,
                "color": theme.TEXT_FAINT,
                "wrap": True,
            },
            {
                "type": "text",
                "text": question.strip() or _BLANK_QUESTION_FALLBACK,
                "size": ft.body,
                "color": theme.TEXT,
                "weight": "bold",
                "wrap": True,
            },
        ],
    }


def _body_text(body: str, ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "text",
        "text": body.strip() or _BLANK_BODY_FALLBACK,
        "size": ft.body,
        "color": theme.TEXT_MUTED,
        "wrap": True,
    }


def _source_buttons(
    sources: Sequence[SourceRef], ft: theme.FlexTheme
) -> list[dict[str, Any]]:
    """把來源做成可點的 URI action 按鈕。

    url 為空的來源略過：URI action 沒有 uri 會被 LINE 拒收。該筆仍存在於
    純文字的來源清單中，符合 rag-responses「缺 url 不得靜默丟棄」的要求——
    這裡略過的是按鈕，不是來源本身。
    """
    return [
        ft.secondary_button(
            f"[{source.index}] {source.label}",
            {"type": "uri", "label": f"[{source.index}]", "uri": source.url},
        )
        for source in sources
        if source.url.strip()
    ]


def _alt_text(header: str, body: str) -> str:
    summary = " ".join((body or "").split())
    text = f"{header}｜{summary}" if summary else header
    return text[:_ALT_TEXT_MAX_LEN]


def _bubble(
    header_title: str,
    question: str,
    body: str,
    buttons: list[dict[str, Any]],
    ft: theme.FlexTheme,
) -> dict[str, Any]:
    contents: list[dict[str, Any]] = [
        _question_block(question, ft),
        _body_text(body, ft),
    ]
    if buttons:
        contents.append({"type": "separator", "margin": "lg", "color": theme.BORDER})
        section = ft.section_title(_SOURCES_LABEL)
        section["margin"] = "lg"
        contents.append(section)

    bubble: dict[str, Any] = {
        "type": "bubble",
        "header": _header(header_title, ft),
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "xl",
            "spacing": "md",
            "contents": contents,
        },
    }
    if buttons:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "lg",
            "contents": buttons,
        }
    return bubble


def build_rag_answer_flex(
    question: str,
    body: str,
    sources: Sequence[SourceRef],
    ft: theme.FlexTheme,
) -> FlexMessage:
    """衛教問答卡（get_rag_answer）。"""
    buttons = _source_buttons(sources, ft)
    bubble = _bubble(_HEADER_RAG, question, body, buttons, ft)
    return FlexMessage(
        altText=_alt_text(_HEADER_RAG, body),
        contents=FlexContainer.from_dict(bubble),
    )


def build_document_answer_flex(
    question: str,
    body: str,
    ft: theme.FlexTheme,
) -> FlexMessage:
    """上傳文件問答卡（answer_from_uploaded_document）。

    沒有來源區段：UserDocumentAnswerService.answer() 只回傳答案本文，不產生
    來源清單。header 文案與衛教卡區隔，避免使用者以為這是知識庫的內容。
    """
    bubble = _bubble(_HEADER_DOCUMENT, question, body, [], ft)
    return FlexMessage(
        altText=_alt_text(_HEADER_DOCUMENT, body),
        contents=FlexContainer.from_dict(bubble),
    )
