"""貼圖的簡短回覆：看 LINE 附上的關鍵字猜貼圖的意思，回一句固定的話。

不進 agent：一張貼圖多半只是打招呼、道謝或表達心情，走完整條問答要多等好幾秒、
多花一次模型呼叫，換來的也只是一句寒暄。

LINE 在貼圖事件裡附最多 15 個描述貼圖的關鍵字（超過 15 個時每次隨機挑 15 個，
同一張貼圖每次拿到的可能不同），官方文件與 SDK 測試資料的範例全是英文，例如
hi、Hello、greetings、Love You
（https://developers.line.biz/en/reference/messaging-api/#wh-sticker）。這個欄位
官方標為實驗性質；沒有關鍵字、或對不上任何一類時，回通用的那句。

訊息貼圖（使用者自己在貼圖上打字的那種）另外帶 text。那是使用者親手打的字，
比關鍵字準，對得上就以它為準；它多半是中文，所以每一類同時列中英文詞。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# 順序就是平手時的優先順序。「難過／不舒服」排在「開心」前面：笑到流淚的貼圖
# 常同時帶 laughing 和 crying，把它問成「怎麼了嗎」只是多關心了一句；把生病的
# 貼圖回成「看到你開心」卻是沒接住對方。
_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "thanks",
        ("thank", "thanks", "thankful", "grateful", "appreciate", "thx", "3q",
         "謝謝", "感謝", "感恩", "多謝", "謝啦"),
    ),
    (
        "sorry",
        ("sorry", "apologize", "apology", "my bad",
         "對不起", "抱歉", "不好意思", "歹勢"),
    ),
    (
        "good_night",
        ("goodnight", "night", "sleep", "sleepy", "bedtime", "zzz",
         "晚安", "睡覺", "想睡", "好睏"),
    ),
    (
        "good_morning",
        ("morning", "早安", "早上好"),
    ),
    (
        "bye",
        ("bye", "goodbye", "see you", "see ya", "掰掰", "拜拜", "再見"),
    ),
    (
        "greeting",
        ("hi", "hello", "hey", "howdy", "yo", "greetings", "whatsup", "what's up", "wave",
         "你好", "您好", "哈囉", "嗨", "安安", "午安"),
    ),
    (
        "unwell",
        ("sad", "cry", "crying", "tears", "sob", "sick", "ill", "unwell", "pain", "hurt",
         "tired", "exhausted", "upset", "depressed", "lonely", "worried", "angry", "mad",
         "fever",
         "難過", "傷心", "哭", "不舒服", "生病", "痛", "累", "生氣", "心情不好", "感冒", "發燒"),
    ),
    (
        "love",
        ("love", "heart", "kiss", "hug", "xoxo", "愛你", "愛心", "抱抱", "親親", "比心"),
    ),
    (
        "happy",
        ("happy", "laugh", "laughing", "lol", "haha", "smile", "smiling", "yay", "joy",
         "glad", "excited",
         "開心", "高興", "快樂", "哈哈", "笑"),
    ),
    (
        "ok",
        ("ok", "okay", "got it", "roger", "yes", "sure", "agree", "good", "great", "nice",
         "cool", "perfect", "thumbs up",
         "好的", "好喔", "好啊", "收到", "讚", "了解", "沒問題", "知道了"),
    ),
)


def _pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    # 英文要整個字比對，否則 hi 會撞到 this、Thinking；中文沒有空白斷詞，只能比對子字串。
    parts = [
        rf"\b{re.escape(term)}\b" if term.isascii() else re.escape(term)
        for term in terms
    ]
    return re.compile("|".join(parts), re.IGNORECASE)


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (category, _pattern(terms)) for category, terms in _CATEGORIES
)

STICKER_REPLY_KEYS: tuple[str, ...] = tuple(
    f"sticker.reply.{category}" for category, _ in _CATEGORIES
) + ("sticker.reply.fallback",)


def classify_sticker(keywords: Iterable[str] | None, text: str | None) -> str | None:
    """回傳貼圖的類別；猜不出來回 None。

    關鍵字是隨機挑的一批，只看第一個對上的會被雜訊帶走，所以數每一類對上幾個
    關鍵字、取最多的那類。
    """
    if text:
        for category, pattern in _PATTERNS:
            if pattern.search(text):
                return category

    best: str | None = None
    best_hits = 0
    for category, pattern in _PATTERNS:
        hits = sum(1 for keyword in keywords or () if pattern.search(keyword))
        if hits > best_hits:
            best, best_hits = category, hits
    return best


def sticker_reply_key(keywords: Iterable[str] | None, text: str | None) -> str:
    """回傳要回覆的那句話的 i18n key。"""
    return f"sticker.reply.{classify_sticker(keywords, text) or 'fallback'}"
