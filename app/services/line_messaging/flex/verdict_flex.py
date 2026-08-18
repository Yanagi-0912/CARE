"""查核判定卡：把查核判定服務的 VerificationResult 組成 LINE Flex Message。

上游 ClaimVerificationService（正規化、比對、同一性驗證、理由改寫）全程只在
繁體中文運作，沒有語言參數——design.md 對這個 change 完全沒提多語系，TFC
本身就是中文查核機構。卡片上的靜態文字因此直接寫死繁中，不像 medication_flex.py
那樣走 t()：把「你問的」「判定來源」這幾個字 i18n 成其他語言，卡片主體的
reasoning 與 related_info 卻仍然是繁中，只會生出半中半英的卡片，比全繁中更難讀。
"""

from __future__ import annotations

from typing import Any, Optional

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.services.rag.claim_verification.service import VerificationResult
from resources.flex_messages import theme

_TFC_SOURCE_LABEL = "台灣事實查核中心"

# 判定字樣 -> 標頭底色（design 決策 6）。事實釐清／證據不足刻意同色：
# 兩者都「不判真偽」，紅綠配色在這兩種情境下反而是誤導。
_VERDICT_COLORS: dict[str, str] = {
    "錯誤": theme.STATUS_CLOSED,
    "部分錯誤": theme.STATUS_PENDING,
    "正確": theme.STATUS_OPEN,
    "事實釐清": theme.STATUS_UNKNOWN,
    "證據不足": theme.STATUS_UNKNOWN,
}

# LINE altText 官方上限 400 字元，超過會讓整則訊息在送出時被拒絕；brief 也只
# 要求「摘要」而非全文，因此摘要後裁切遠比讓推播失敗安全。
_ALT_TEXT_MAX_LEN = 400


def _header(verdict: str, ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        # 不認得的判定字串防禦性退回中性灰，而非讓整張卡片因缺色碼而組裝失敗——
        # verdict 理論上只會是 service.py 定義的五種之一，但呈現層不該把上游
        # 契約沒顧到的壞資料放大成「整張卡片消失」。
        "backgroundColor": _VERDICT_COLORS.get(verdict, theme.STATUS_UNKNOWN),
        "paddingAll": "lg",
        "contents": [
            {
                "type": "text",
                "text": verdict,
                "color": theme.TEXT_ON_BRAND,
                "weight": "bold",
                "size": ft.heading,
                "wrap": True,
            }
        ],
    }


def _question_block(user_question: str, ft: theme.FlexTheme) -> dict[str, Any]:
    """使用者原問句區塊。顯示這個而非知識庫的 claim 是刻意的設計決策（design
    決策 8）：線上實測 340 篇有 claim 的 TFC 文章裡，35% 的 claim 裝的其實是
    查核結論，會跟卡片另外呈現的判定重複且語意打架；使用者要的也是「我問的
    這件事」，不是「TFC 當初怎麼記錄這則謠言」。
    """
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
                "text": "你問的",
                "size": ft.caption,
                "color": theme.TEXT_FAINT,
                "wrap": True,
            },
            {
                "type": "text",
                "text": user_question,
                "size": ft.body,
                "weight": "bold",
                "color": theme.TEXT,
                "wrap": True,
            },
        ],
    }


def _paragraph(
    text: str, ft: theme.FlexTheme, color: str = theme.TEXT_MUTED, **extra: Any
) -> dict[str, Any]:
    return {
        "type": "text",
        "text": text,
        "size": ft.body,
        "color": color,
        "wrap": True,
        **extra,
    }


def _source_note(ft: theme.FlexTheme) -> list[dict[str, Any]]:
    """命中時的來源標示（design 決策 5）：系統是在轉述 TFC 的判定，不是 CARE
    自己查核出來的結論，卡片必須讓使用者看得出這件事——這行文字與 source_url
    是否有值無關，一律要出現。
    """
    return [
        theme.divider(),
        _paragraph(f"判定來源：{_TFC_SOURCE_LABEL}", ft, margin="lg"),
    ]


def _source_button(source_url: str, ft: theme.FlexTheme) -> Optional[dict[str, Any]]:
    """來源報告的可點按鈕；source_url 為空（理論上 matched=True 必有值，見
    service.py 的欄位註解，這裡仍防禦性處理）時回傳 None，呼叫端據此完全不
    產生 footer。LINE 對帶空字串 uri 的 action 會拒收整則 Flex Message，寧可
    少一顆按鈕也不能讓整張卡片送不出去——做法比照 official_site_tools：先判斷
    有沒有可用網址，再決定要不要進入會產生 action 的分支，而不是把空字串
    一路傳進 action 再指望某處攔截。
    """
    url = source_url.strip()
    if not url:
        return None
    label = "查看查核報告"
    return ft.secondary_button(
        f"{label} →", {"type": "uri", "label": label, "uri": url}
    )


def _related_info_block(related_info: str, ft: theme.FlexTheme) -> list[dict[str, Any]]:
    """未命中時的相關衛教資訊（design 決策 4）。標題與說明都要讓使用者看得出
    這不是這次說法的查核依據，只是資料庫裡查得到的參考資訊，避免「證據不足」
    被誤讀成「這份衛教資訊就是查核結果」。
    """
    return [
        theme.divider(),
        _paragraph("相關衛教資訊", ft, color=theme.TEXT, weight="bold", margin="lg"),
        _paragraph("僅供參考，非本次說法的查核依據。", ft, color=theme.TEXT_FAINT),
        _paragraph(related_info, ft),
    ]


def _body(contents: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "xl",
        "backgroundColor": theme.SURFACE,
        "spacing": "md",
        "contents": contents,
    }


def _footer(button: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "lg",
        "contents": [button],
    }


def _alt_text(result: VerificationResult) -> str:
    """通知列與不支援 Flex 的環境仍要讀得出判定與問句摘要，因此含判定字樣，
    並裁切至 LINE altText 的官方上限（見 `_ALT_TEXT_MAX_LEN`）。
    """
    text = f"查核判定：{result.verdict}｜{result.user_question}"
    return text[:_ALT_TEXT_MAX_LEN]


def build_verdict_flex(
    result: VerificationResult, font_size: str | None = None
) -> FlexMessage:
    """把一次查核結果組成判定卡。

    `matched` 決定卡片下半部接的是「來源標示＋按鈕」還是「相關衛教資訊」，
    兩者互斥：命中代表判定轉述自 TFC，該附可查證的原文連結；未命中沒有來源
    可附，改附一般衛教檢索到的參考資訊（見 service.py 的 VerificationResult
    欄位註解與 design 決策 4）。
    """
    ft = theme.resolve_theme(font_size)

    body_contents: list[dict[str, Any]] = [
        _question_block(result.user_question, ft),
        _paragraph(result.reasoning, ft, margin="md"),
    ]

    footer_button: Optional[dict[str, Any]] = None
    if result.matched:
        body_contents.extend(_source_note(ft))
        footer_button = _source_button(result.source_url, ft)
    elif result.related_info:
        body_contents.extend(_related_info_block(result.related_info, ft))

    bubble_dict: dict[str, Any] = {
        "type": "bubble",
        "header": _header(result.verdict, ft),
        "body": _body(body_contents),
    }
    # footer 整段只在有可用連結時才加入——不是加入一個沒有 action 的 footer，
    # 兩者對 LINE 而言不同：前者才是真正符合「不含任何 action」的版面。
    if footer_button is not None:
        bubble_dict["footer"] = _footer(footer_button)

    container = FlexContainer.from_dict(bubble_dict)
    return FlexMessage(altText=_alt_text(result), contents=container)
