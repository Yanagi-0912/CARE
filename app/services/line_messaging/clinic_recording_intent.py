"""「看診錄音」的關鍵字判斷：整句就是要開始錄看診，直接進錄音流程、不進 agent。

理由同分享卡（見 share_intent）：回應固定是徵詢同意的那一則，走完整條問答只是多等。
只攔整句，句子裡還有別的內容（「看診錄音可以給醫生聽嗎」）就交給 agent。

各語言的說法要跟 `clinic.chat.expired` 裡叫使用者再傳一次的那句一致。
"""

from __future__ import annotations

import re
import unicodedata

_NOISE_RE = re.compile(r"[\s，,。．.！!？?～~、:：;；…\-—「」『』()（）\"]+")

_PREFIX = r"(?:我要|我想要|我想|請|幫我|開始|要)?"

_PATTERNS = (
    rf"^{_PREFIX}(?:看診|陪診|門診|診間)?錄音(?:一下)?$",
    rf"^{_PREFIX}錄(?:看診|門診)$",
    r"^record(?:the)?(?:visit|appointment)$",
    r"^rekamkunjungan$",
    r"^ghiâmbuổikhám$",
    r"^บันทึกการตรวจ$",
    r"^診察を録音(?:する)?$",
)
_INTENT_RE = re.compile("|".join(_PATTERNS))


def is_clinic_recording_intent(text: str) -> bool:
    if not text:
        return False
    normalized = _NOISE_RE.sub("", unicodedata.normalize("NFKC", text).lower())
    return bool(_INTENT_RE.match(normalized))
