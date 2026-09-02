"""查核判定卡的 agent tool：接住「查證特定說法」的問句，轉呼叫 ClaimVerificationService。"""

import json
import logging

from langchain_core.tools import tool

from app.services.line_messaging.flex.verdict_flex import build_verdict_flex
from app.services.rag.claim_verification.service import VerificationResult
from resources.flex_messages.size_guard import fits

logger = logging.getLogger(__name__)

_claim_verification_service = None

_TFC_SOURCE_LABEL = "台灣事實查核中心"


def configure_claim_tool(claim_verification_service) -> None:
    """DI 初始化時呼叫，注入 ClaimVerificationService 實例。"""
    global _claim_verification_service
    _claim_verification_service = claim_verification_service


def is_claim_tool_configured() -> bool:
    """`verify_claim` 是否已被注入可用的服務。

    `CLAIM_VERIFICATION_ENABLED` 唯一的生效點是 dependencies.py 組裝時
    「要不要呼叫 `configure_claim_tool`」；registry.py 讀這個函式來決定
    要不要提供 `verify_claim`，而不是自己再讀一次那個設定值，這樣同一個
    判斷才不會分散在兩個檔案裡各自維護一份，也讓 registry.py 的測試能純粹
    透過 `configure_claim_tool` 這個既有的 DI 注入點切換兩種狀態，
    不必 monkeypatch 全域的 settings 單例。
    """
    return _claim_verification_service is not None


def _format_verdict_reply(result: VerificationResult) -> str:
    """純文字判定卡：Flex 版判定卡組裝失敗時的 fallback，仍須符合
    line-reply-rules 的「不得輸出 Markdown」。這是 Flex 化之前唯一的輸出
    格式，保留它而非刪除，是因為它是「Flex 組裝出錯也不能讓使用者拿到
    例外」這條防線的最後一層（見 `verify_claim` 的 try/except）。

    「來源」與「相關衛教資訊」互斥：命中時判定逐字取自 TFC，附上可查證的
    原文連結；未命中時改附一般衛教檢索到的相關資訊，避免「證據不足」變成
    使用者什麼都拿不到。

    未命中側的衛教資訊同樣要列出出處。缺 url 的來源只列名稱、不列網址，
    但不得省略——與 Flex 版一致，也是 rag-responses 對來源呈現的既有要求。
    """
    lines = [
        f"判定：{result.verdict}",
        f"你問的：{result.user_question}",
        "",
        result.reasoning,
    ]
    if result.matched:
        lines.extend(["", f"資料來源：{_TFC_SOURCE_LABEL}", result.source_url])
    elif result.related_info:
        lines.extend(["", "相關衛教資訊：", result.related_info])
        if result.related_sources:
            lines.extend(["", "資料來源："])
            lines.extend(
                f"[{source.index}] {source.label}：{source.url}"
                if source.url.strip()
                else f"[{source.index}] {source.label}"
                for source in result.related_sources
            )
    return "\n".join(lines)


def _format_verdict_speech(result: VerificationResult) -> str:
    """判定卡的朗讀稿：耳朵拿到的內容要與眼睛看到的卡片一致。

    與 `_format_verdict_reply` 分開而不共用，差別只在**網址**：純文字
    fallback 是給眼睛看的，URL 可以點；把一長串 URL 念出來只是噪音，要點的
    人點卡片上的來源按鈕。因此網址不入稿，出處只念名稱。

    未命中側的 `related_info` 必須入稿。這張卡的全部價值就在那段衛教資訊上
    ——`_NO_MATCH_REASONING` 本文就寫著「以下提供資料庫中相關的衛教資訊供
    參考」，念完這句卻不給內容，等於語音自己開了一張空頭支票，比少念一段
    更糟。卡片是用 `_paragraph(related_info, ft)` 整段渲染的（見
    `verdict_flex._related_info_block`），朗讀稿跟著給整段才對得上。

    長度是有界的，不是無上限：`_fetch_related_info` 最多取
    `_RELATED_INFO_TOP_K`（2）段、一篇一段，每段是檢索 chunk（`chunk_size`
    上限 500 字），所以最壞情況約在千餘字這個量級，與卡片受
    `SAFE_BUBBLE_BYTES` 約束的上限是同一個。真正的音檔長度取決於語音引擎的
    語速，本專案沒有量過——`tts_service._get_duration_ms` 裡那個每字 250ms
    只是讀不到 MP3 metadata 時的估算 fallback，不是實測值，不要拿它當依據。
    要設上限的話，該設在卡片與朗讀稿共同的源頭（`_RELATED_INFO_TOP_K`），
    而不是只截朗讀稿——那會把「眼睛與耳朵不一致」這個剛修掉的坑再挖一次。
    """
    lines = [f"判定：{result.verdict}", result.reasoning]
    if result.matched:
        lines.append(f"資料來源：{_TFC_SOURCE_LABEL}")
    elif result.related_info:
        lines.extend(["相關衛教資訊：", result.related_info])
    return "\n".join(line for line in lines if line and line.strip())


def _to_flex_message_text(result: VerificationResult) -> str | None:
    """把判定卡組成 LINE Flex Message JSON 字串；超過大小門檻時回傳 None。

    格式比照 `official_site_tools.open_official_site`：
    `{"type": "flex", "altText": ..., "contents": {...}}`，這是
    `reply.py._try_parse_flex_message` 認得、會還原成真正 FlexMessage 送出的
    形狀，另外多帶一個 `speechText` 供語音回覆使用（見下方註解）。`app/services/agent/agent.py` 的 `medical_tool_names` 另外會把這個
    字串直接當成最終回覆、跳過模型再次改寫（見該處註解），因此這裡的輸出
    格式必須與其他 Flex 工具一致，不能只是「看起來像 JSON」。

    大小檢查在這裡而非 `build_verdict_flex` 裡：退回純文字的決策點在本模組
    （`_format_verdict_reply` 已是既有的 fallback），builder 維持只負責組裝
    的單一職責。回傳 None 而非拋例外，是為了讓「太大」與「組裝壞掉」在
    `verify_claim` 裡分別留下不同的 log——兩者都退回純文字，但成因不同。
    """
    flex_message = build_verdict_flex(result)
    payload = flex_message.to_dict()
    if not fits(payload["contents"]):
        return None
    # 朗讀稿跟著 payload 一起過去，因為 `reply.py` 只收得到這個字串：判定卡是
    # 工具自己組的，純文字版本不會跨過 agent 邊界（RAG 那條路相反——卡片由
    # replier 自己組，所以它手上還留著組卡前的文字可以念）。少了這個鍵，開了
    # 語音回覆的使用者會在判定卡上靜默失去語音，和 RAG 回答卡當初的坑一樣。
    #
    # 多一個頂層鍵不影響 `_try_parse_flex_message`（它只讀 type/altText/
    # contents），也不影響上面的大小檢查——LINE 的 bubble 上限算的是 contents，
    # 這個鍵在 contents 之外，送出前就被 replier 拆掉了。
    payload["speechText"] = _format_verdict_speech(result)
    return json.dumps(payload, ensure_ascii=False)


@tool
async def verify_claim(query: str) -> str:
    """當使用者要查證某個特定說法是真是假時呼叫。典型句型是「網傳⋯是真的
    嗎」「聽說⋯真的假的」「我朋友說⋯」。回傳台灣事實查核中心既有的查核
    結論，不做即時真假判斷；查核中心沒查過的說法會回「證據不足」。

    若問題問的是衛教知識本身而非查證特定說法（例如「⋯有哪些症狀」
    「⋯多久做一次」「⋯可以吃嗎」），請改用 get_rag_answer。
    """
    if _claim_verification_service is None:
        return "查核判定服務未初始化，請稍後再試。"
    result = await _claim_verification_service.verify(query)
    try:
        flex_text = _to_flex_message_text(result)
    except Exception:  # noqa: BLE001
        # Flex 組裝是呈現層的最後一步，任何非預期例外都不該讓使用者拿到堆疊
        # 追蹤或空白回覆；退回 Flex 化之前就存在的純文字格式，判定內容仍能
        # 送到使用者手上。
        logger.warning("判定卡 Flex 組裝失敗，改回純文字格式", exc_info=True)
        return _format_verdict_reply(result)

    if flex_text is None:
        # 超過 LINE 的 bubble 上限。硬送出去會在 reply_message() 被以 400
        # 拒收，例外被 reply() 的 except 吞掉後使用者什麼都收不到，比純文字
        # 糟得多。會超標的通常是未命中側：related_info 雖然有界（
        # `_RELATED_INFO_TOP_K` 段 × chunk 上限 500 字），但那個上限是照著
        # `SAFE_BUBBLE_BYTES` 的餘裕挑的，只剩 223 bytes（見
        # `claim_verification/service.py` 該常數上方的實測表），標題偏長時
        # 仍會擠爆。此處原本寫「衛教文章全文、沒有長度上限」，與
        # `_fetch_related_info` 的實作不符，一併更正。
        logger.warning(
            "判定卡超過 Flex 大小上限，改回純文字格式，matched=%s", result.matched
        )
        return _format_verdict_reply(result)

    return flex_text
