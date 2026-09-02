import json

from resources.flex_messages.size_guard import (
    LINE_BUBBLE_LIMIT_BYTES,
    SAFE_BUBBLE_BYTES,
    fits,
    wire_bytes,
)


def _bubble(text: str) -> dict:
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [{"type": "text", "text": text, "wrap": True}],
        },
    }


def test_wire_bytes_counts_non_ascii_as_escaped():
    """中文在上線時是 \\uXXXX（6 bytes），不是 UTF-8 的 3 bytes。

    這是本模組存在的理由：linebot/v3/messaging/rest.py:155 用
    json.dumps(body) 的預設 ensure_ascii=True 送出。若改用未轉義的
    UTF-8 計算，會低估一倍，防線等於失效。
    """
    one_char = _bubble("衛")
    ten_chars = _bubble("衛" * 10)

    assert wire_bytes(ten_chars) - wire_bytes(one_char) == 9 * 6
    assert wire_bytes(ten_chars) > len(
        json.dumps(ten_chars, ensure_ascii=False).encode()
    )


def test_safe_threshold_leaves_headroom_below_line_limit():
    assert SAFE_BUBBLE_BYTES < LINE_BUBBLE_LIMIT_BYTES
    assert LINE_BUBBLE_LIMIT_BYTES == 10 * 1024


def test_fits_accepts_small_bubble():
    assert fits(_bubble("蜂蜜不需要放冰箱。")) is True


def test_fits_rejects_oversized_bubble():
    assert fits(_bubble("衛" * 3000)) is False


def test_fits_honours_explicit_limit():
    bubble = _bubble("衛" * 100)
    assert fits(bubble, limit=wire_bytes(bubble)) is True
    assert fits(bubble, limit=wire_bytes(bubble) - 1) is False
