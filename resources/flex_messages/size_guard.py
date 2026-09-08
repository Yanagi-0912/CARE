"""Flex bubble 送出前的大小防線。

LINE 對單一 bubble 的 JSON 有 10 KB 上限，超過會在 reply_message() 被以
400 拒收。那個例外會被 reply.py 的 except 接住，使用者最後什麼都收不到——
比退回純文字糟得多，因此要在送出前先擋下來。

量測必須與實際送出的序列化一致：linebot/v3/messaging/rest.py:155 是
`json.dumps(body)`，用預設的 ensure_ascii=True，因此每個中文字在傳輸時是
`\\uXXXX` 共 6 bytes，不是 UTF-8 的 3 bytes。用 ensure_ascii=False 計算會
低估一倍，等於沒有防線。
"""

from __future__ import annotations

import json
from typing import Any

# LINE 官方對單一 bubble 的 JSON 大小上限。
LINE_BUBBLE_LIMIT_BYTES = 10 * 1024

# 本專案的門檻，比官方上限低約 10%。留餘裕的理由：LINE 側是否以完全相同的
# 方式計算大小並未見於文件，貼著上限送等於把「使用者收不到回覆」的風險押在
# 一個沒有保證的假設上。
SAFE_BUBBLE_BYTES = 9 * 1024


def wire_bytes(bubble: dict[str, Any]) -> int:
    """回傳這個 bubble 實際送出時佔用的位元組數。"""
    return len(json.dumps(bubble).encode("utf-8"))


def fits(bubble: dict[str, Any], limit: int = SAFE_BUBBLE_BYTES) -> bool:
    """這個 bubble 是否可安全送出。"""
    return wire_bytes(bubble) <= limit
