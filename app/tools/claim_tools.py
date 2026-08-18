"""查核判定卡的 agent tool：接住「查證特定說法」的問句，轉呼叫 ClaimVerificationService。"""

import json
import logging

from langchain_core.tools import tool

from app.services.line_messaging.flex.verdict_flex import build_verdict_flex
from app.services.rag.claim_verification.service import VerificationResult

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
    原文連結；未命中時沒有來源可附，改附一般衛教檢索到的相關資訊，避免
    「證據不足」變成使用者什麼都拿不到。
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
    return "\n".join(lines)


def _to_flex_message_text(result: VerificationResult) -> str:
    """把判定卡組成 LINE Flex Message JSON 字串。

    格式比照 `official_site_tools.open_official_site`：
    `{"type": "flex", "altText": ..., "contents": {...}}`，這是
    `reply.py._try_parse_flex_message` 認得、會還原成真正 FlexMessage 送出的
    形狀。`app/services/agent/agent.py` 的 `medical_tool_names` 另外會把這個
    字串直接當成最終回覆、跳過模型再次改寫（見該處註解），因此這裡的輸出
    格式必須與其他 Flex 工具一致，不能只是「看起來像 JSON」。
    """
    flex_message = build_verdict_flex(result)
    return json.dumps(flex_message.to_dict(), ensure_ascii=False)


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
        return _to_flex_message_text(result)
    except Exception:  # noqa: BLE001
        # Flex 組裝是呈現層的最後一步，任何非預期例外都不該讓使用者拿到堆疊
        # 追蹤或空白回覆；退回 Flex 化之前就存在的純文字格式，判定內容仍能
        # 送到使用者手上。
        logger.warning("判定卡 Flex 組裝失敗，改回純文字格式", exc_info=True)
        return _format_verdict_reply(result)
