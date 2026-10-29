"""「功能說明」的關鍵字判斷：整句就是在問 CARE 能做什麼，直接回歡迎卡、不進 agent。

加好友只會收到一次歡迎卡，滑掉就找不回來；這裡讓使用者之後還叫得出同一份。
不交給 agent 的理由同分享卡（見 share_intent）：答案固定是同一張卡，而且 prompt
裡沒有功能清單，模型自己介紹只會漏講或講錯。

只攔整句，而且句子裡要有「功能」「CARE」「你」這類指明在問 CARE 本身的字：
「怎麼用？」「請說明」單獨出現時多半是在追問上一則（吸入器怎麼用、剛才那段再
解釋一次），攔下來回功能卡就答非所問。

刻意不收「help」「救命」「bantuan」「trợ giúp」「ช่วยเหลือ」「ヘルプ」這類同時也是
求救的字：這裡排在走失與急迫度判斷之前，求救的人拿到一張功能介紹卡就太糟了。

歡迎卡上 `welcome.recall_hint` 叫使用者傳的那句，每種語言都要在這裡認得
（test_feature_guide_intent 會檢查）。
"""

from __future__ import annotations

import re
import unicodedata

# 比對前拿掉的字元：空白與常見的中英文標點（同 share_intent）。
_NOISE_RE = re.compile(r"[\s，,。．.！!？?～~、:：;；…\-—「」『』()（）\"“”]+")

_PREFIX = r"(?:請問|請|我想看|我要看|給我看)?"

_PATTERNS = (
    # 中文。「你能做什麼」要有主詞：「我可以做什麼」是在問病情，不是問 CARE。
    r"(?:功能|使用)說明(?:書)?|說明書",
    r"功能(?:介紹|列表|清單|總覽)?|(?:你|care)?(?:有)?(?:什麼|甚麼|哪些)功能",
    r"(?:使用|新手)教學",
    r"care(?:要)?怎麼(?:使)?用|(?:要)?怎麼(?:使)?用care",
    r"(?:你|care)(?:會|能|可以)(?:幫我)?(?:做|幫忙)?(?:什麼|甚麼|啥|哪些)(?:事|事情)?",
    # 英文、印尼文、越南文、泰文、日文。空白已先去掉。
    r"features|whatcanyoudo|userguide|howtousecare",
    r"fitur|fiturcare|apayangbisakamulakukan",
    r"(?:các)?tínhnăng|bạn(?:cóthể)?làm(?:được)?gì",
    r"ฟีเจอร์|คุณทำอะไรได้บ้าง",
    r"機能(?:一覧)?|何ができる(?:の|ますか)?|careの使い方",
)

_SUFFIX = r"(?:嗎|呢|啊|呀)?"

_INTENT_RE = re.compile(rf"^{_PREFIX}(?:{'|'.join(_PATTERNS)}){_SUFFIX}$")


def is_feature_guide_intent(text: str) -> bool:
    """整句就是在問 CARE 有哪些功能、怎麼用。"""
    if not text:
        return False
    normalized = _NOISE_RE.sub("", unicodedata.normalize("NFKC", text).lower())
    return bool(_INTENT_RE.match(normalized))
