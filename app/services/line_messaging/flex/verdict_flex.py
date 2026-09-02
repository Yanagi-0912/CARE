"""查核判定卡：把查核判定服務的 VerificationResult 組成 LINE Flex Message。

上游 ClaimVerificationService（正規化、比對、同一性驗證、理由改寫）全程只在
繁體中文運作，沒有語言參數——design.md 對這個 change 完全沒提多語系，TFC
本身就是中文查核機構。卡片上的靜態文字因此直接寫死繁中，不像 medication_flex.py
那樣走 t()：把「你問的」「判定來源」這幾個字 i18n 成其他語言，卡片主體的
reasoning 與 related_info 卻仍然是繁中，只會生出半中半英的卡片，比全繁中更難讀。
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.core.rag_sources import SourceRef
from app.services.rag.claim_verification.service import (
    NOT_ENOUGH_EVIDENCE_SLUG,
    VerificationResult,
)
from resources.flex_messages import theme

_TFC_SOURCE_LABEL = "台灣事實查核中心"
_RELATED_SOURCES_LABEL = "資料來源"

# CARE-data 舊站遷移文章的 verdict_slug 可能帶這個前綴（例如 "legacy:錯誤"），
# 是舊站資料路徑留下的形式，不是本模組自己定義的格式。
_LEGACY_SLUG_PREFIX = "legacy:"

# verdict_slug（穩定機器鍵）-> 標頭底色，是配色表的主要依據（I4 finding）。
# 中文顯示字串來自 CARE-data 的前綴對照表或 TFC 網站用詞，兩者都出過資料
# 異常事故（漏收「正確」前綴導致 verdict 寫成 None、值帶空白），slug 是
# 系統間約定的機器鍵，較不受這類上游用詞漂移影響——matcher.py 執行期已經
# 檢核 verdict 屬於五個合法值，這裡是同一道防線在呈現層的延伸。
_SLUG_COLORS: dict[str, str] = {
    "incorrect": theme.STATUS_CLOSED,
    "partially-incorrect": theme.STATUS_PENDING,
    "correct": theme.STATUS_OPEN,
    "clarification": theme.STATUS_UNKNOWN,
    "unproven": theme.STATUS_UNKNOWN,
    NOT_ENOUGH_EVIDENCE_SLUG: theme.STATUS_UNKNOWN,
}

# 判定字樣 -> 標頭底色（design 決策 6）。事實釐清／證據不足刻意同色：
# 兩者都「不判真偽」，紅綠配色在這兩種情境下反而是誤導。這個表只作為
# verdict_slug 缺漏（存量資料、尚未回填）或無法辨識時的備援，不是主要
# 依據——中文字串正是 I4 finding 指出會出錯的那一層，不能反過來當第一
# 順位查找。
_VERDICT_TEXT_COLORS: dict[str, str] = {
    "錯誤": theme.STATUS_CLOSED,
    "部分錯誤": theme.STATUS_PENDING,
    "正確": theme.STATUS_OPEN,
    "事實釐清": theme.STATUS_UNKNOWN,
    "證據不足": theme.STATUS_UNKNOWN,
}

# LINE altText 官方上限 400 字元，超過會讓整則訊息在送出時被拒絕；brief 也只
# 要求「摘要」而非全文，因此摘要後裁切遠比讓推播失敗安全。
_ALT_TEXT_MAX_LEN = 400

# user_question／reasoning 為空字串時的顯示備援。LINE Flex 的 text 元件與
# altText 都要求非空字串，空字串會讓整則訊息在 API 呼叫時直接被拒收
# （400），使用者什麼都收不到——比顯示一句不完美的預設文字更糟（C2
# finding）。理論上 service.py 目前的實作不會產生空字串（matcher 對空
# content 已視為未命中、未命中理由是固定句、理由改寫失敗有中性 fallback），
# 這裡仍防禦性地擋一次：呈現層不該把「上游承諾不會給空字串」當成保證。
_BLANK_QUESTION_FALLBACK = "（無法取得原始問句內容）"
_BLANK_REASONING_FALLBACK = "（暫無查核說明，請點選下方連結查看原文）"


def _resolve_header_color(verdict: str, verdict_slug: str) -> str:
    """決定標頭底色：verdict_slug 是主要依據，中文顯示字串只在 slug 缺漏或
    無法辨識時才當備援（I4 finding）。"""
    slug = (verdict_slug or "").strip()
    if slug.startswith(_LEGACY_SLUG_PREFIX):
        slug = slug[len(_LEGACY_SLUG_PREFIX) :]
    if slug in _SLUG_COLORS:
        return _SLUG_COLORS[slug]
    if slug in _VERDICT_TEXT_COLORS:  # "legacy:錯誤" 剝除前綴後就是中文字串
        return _VERDICT_TEXT_COLORS[slug]
    # 不認得的 slug（含空字串）退回中文字串比對，中文字串也認不得時再退回
    # 中性灰——不能讓整張卡片因缺色碼而組裝失敗。
    return _VERDICT_TEXT_COLORS.get(verdict, theme.STATUS_UNKNOWN)


def _header(verdict: str, verdict_slug: str, ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": _resolve_header_color(verdict, verdict_slug),
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


def _source_note(ft: theme.FlexTheme, published_at: str = "") -> list[dict[str, Any]]:
    """命中時的來源標示（design 決策 5）：系統是在轉述 TFC 的判定，不是 CARE
    自己查核出來的結論，卡片必須讓使用者看得出這件事——這行文字與 source_url
    是否有值無關，一律要出現。

    有發布日期時一併顯示。刻意只呈現、不由系統依日期篩選：查核報告不會過期，
    2021 年查核過的謠言在 2026 年重傳時那份報告依然有效，用日期硬篩會擋掉
    大量仍然正確的答案。這則查核有多新該由使用者自己判斷。
    """
    label = _TFC_SOURCE_LABEL
    date = (published_at or "").strip()
    if date:
        label = f"{label}（{date} 發布）"
    return [
        theme.divider(),
        _paragraph(f"判定來源：{label}", ft, margin="lg"),
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


def _related_info_block(
    related_info: str, sources: Sequence[SourceRef], ft: theme.FlexTheme
) -> list[dict[str, Any]]:
    """未命中時的相關衛教資訊（design 決策 4）。標題與說明都要讓使用者看得出
    這不是這次說法的查核依據，只是資料庫裡查得到的參考資訊，避免「證據不足」
    被誤讀成「這份衛教資訊就是查核結果」。

    出處以「[n] 來源名」的文字清單呈現，**每一筆都列出、含沒有網址的那些**
    ——rag-responses 明文要求缺 url 的來源不得靜默丟棄，呈現層只是不為它
    產生按鈕（做法與 rag_answer_flex 一致）。編號與 footer 按鈕上的 [n]
    對應同一筆，見 `SourceRef.index` 的 docstring。

    免責說明排在出處之前是刻意的：決策 4 擔心的正是使用者把附帶資訊誤讀成
    查核依據，而「看起來可查證的來源」是最容易造成那種誤讀的元素——先說清楚
    這不是查核依據，再給來源。
    """
    contents = [
        theme.divider(),
        _paragraph("相關衛教資訊", ft, color=theme.TEXT, weight="bold", margin="lg"),
        _paragraph("僅供參考，非本次說法的查核依據。", ft, color=theme.TEXT_FAINT),
        _paragraph(related_info, ft),
    ]
    labels = "、".join(f"[{source.index}] {source.label}" for source in sources)
    if labels:
        contents.append(
            _paragraph(
                f"{_RELATED_SOURCES_LABEL}：{labels}",
                ft,
                color=theme.TEXT_FAINT,
                size=ft.caption,
                margin="md",
            )
        )
    return contents


def _related_source_buttons(
    sources: Sequence[SourceRef], ft: theme.FlexTheme
) -> list[dict[str, Any]]:
    """相關衛教資訊的可點來源；url 為空者略過。

    略過的是按鈕、不是來源本身——那些來源仍列在 `_related_info_block` 的文字
    清單裡。這個區分很重要：「食藥署公告」那 576 篇上游結構上就沒有網址，
    若連列都不列，等於整批來源在呈現層消失。

    空字串 uri 會讓 LINE 拒收整則 Flex Message，理由與做法同 `_source_button`。
    """
    return [
        ft.secondary_button(
            f"[{source.index}] {source.label}",
            {"type": "uri", "label": f"[{source.index}]", "uri": source.url},
        )
        for source in sources
        if source.url.strip()
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


def _footer(buttons: list[dict[str, Any]]) -> dict[str, Any]:
    """footer 收一到多顆按鈕：命中側只有一顆查核報告連結，未命中側則是
    相關衛教資訊的來源，數量隨 `_RELATED_INFO_TOP_K` 而定。"""
    return {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "paddingAll": "lg",
        "contents": buttons,
    }


def _alt_text(result: VerificationResult, user_question: str) -> str:
    """通知列與不支援 Flex 的環境仍要讀得出判定與問句摘要，因此含判定字樣，
    並裁切至 LINE altText 的官方上限（見 `_ALT_TEXT_MAX_LEN`）。

    `user_question` 是呼叫端已做過空字串防護的版本（見 `build_verdict_flex`），
    不是 `result.user_question` 原始值——altText 本身因為有「查核判定：」
    這個固定前綴，即使問句為空也不會整串變成空字串，但沿用同一份防護後的
    文字仍能避免尾端多一個沒有內容的分隔符號。
    """
    text = f"查核判定：{result.verdict}｜{user_question}"
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

    # 空字串防護見 `_BLANK_QUESTION_FALLBACK`／`_BLANK_REASONING_FALLBACK`
    # 的模組層註解（C2 finding）。
    user_question = result.user_question.strip() or _BLANK_QUESTION_FALLBACK
    reasoning = result.reasoning.strip() or _BLANK_REASONING_FALLBACK

    body_contents: list[dict[str, Any]] = [
        _question_block(user_question, ft),
        _paragraph(reasoning, ft, margin="md"),
    ]

    footer_buttons: list[dict[str, Any]] = []
    if result.matched:
        body_contents.extend(_source_note(ft, result.source_published_at))
        matched_button = _source_button(result.source_url, ft)
        if matched_button is not None:
            footer_buttons.append(matched_button)
    elif result.related_info:
        body_contents.extend(
            _related_info_block(result.related_info, result.related_sources, ft)
        )
        footer_buttons.extend(_related_source_buttons(result.related_sources, ft))

    bubble_dict: dict[str, Any] = {
        "type": "bubble",
        "header": _header(result.verdict, result.verdict_slug, ft),
        "body": _body(body_contents),
    }
    # footer 整段只在有可用連結時才加入——不是加入一個沒有 action 的 footer，
    # 兩者對 LINE 而言不同：前者才是真正符合「不含任何 action」的版面。
    # 未命中側的來源全都沒有網址時（例如整批命中「食藥署公告」），這裡同樣
    # 不產生 footer，但那些來源已經列在 body 的文字清單中，沒有被丟掉。
    if footer_buttons:
        bubble_dict["footer"] = _footer(footer_buttons)

    container = FlexContainer.from_dict(bubble_dict)
    return FlexMessage(altText=_alt_text(result, user_question), contents=container)
