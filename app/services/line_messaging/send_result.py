"""LINE Messaging API 送出結果的分類。

reply／push 以前一律回 bool，429（額度用完）、400（對象無效或內容不合法）、
401（token 失效）、網路逾時全部長得一樣。排程器需要分辨「額度用完」和「使用者
沒吃藥」，webhook 需要分辨「reply token 過期」和「內容不合法」，這裡把分類
集中在一處，呼叫端拿到 `SendResult` 就能決定要不要改走 push、要不要重試。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class SendOutcome(str, Enum):
    OK = "ok"
    # 429：本月推播額度用完或短時間內打太快。重送沒有意義，也不該記成「已送出」。
    QUOTA_EXCEEDED = "quota_exceeded"
    # 400：LINE 明確拒絕這一次的內容或對象（reply token 過期／已用、訊息格式錯、
    # 對象不存在）。同樣的內容再送一次也會失敗。
    REJECTED = "rejected"
    # 401：channel access token 失效（console 撤銷或重發）。呼叫端應清掉快取。
    UNAUTHORIZED = "unauthorized"
    # 其他：LINE 5xx、逾時、DNS，屬於暫時性，重試有機會成功。
    TRANSIENT = "transient"


@dataclass(frozen=True)
class SendResult:
    outcome: SendOutcome
    status: Optional[int] = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is SendOutcome.OK

    @property
    def retryable(self) -> bool:
        """暫時性失敗才值得原封重送；額度、內容、token 問題重送只是白花一次。"""
        return self.outcome is SendOutcome.TRANSIENT

    @classmethod
    def success(cls) -> "SendResult":
        return cls(SendOutcome.OK)


def classify_send_exception(exc: BaseException) -> SendResult:
    """把 LINE SDK 或網路層的例外翻成 SendResult。

    `linebot.v3.messaging.exceptions.ApiException` 帶 `.status`；SDK 版本或
    mock 沒有 status 時一律當暫時性失敗，寧可多重試也不要把真失敗記成已送。
    """
    status = _extract_status(exc)
    detail = _short_detail(exc)
    if status == 429:
        return SendResult(SendOutcome.QUOTA_EXCEEDED, status, detail)
    if status == 401:
        return SendResult(SendOutcome.UNAUTHORIZED, status, detail)
    if status in (400, 404):
        return SendResult(SendOutcome.REJECTED, status, detail)
    return SendResult(SendOutcome.TRANSIENT, status, detail)


def _extract_status(exc: BaseException) -> Optional[int]:
    status: Any = getattr(exc, "status", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    return None


def _short_detail(exc: BaseException) -> str:
    body = getattr(exc, "body", None)
    text = body if isinstance(body, str) else str(exc)
    return text[:200]
