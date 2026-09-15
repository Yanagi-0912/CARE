"""分享卡的關鍵字判斷。

使用者想把 CARE 推薦給朋友、要加好友連結或 QR code，或想邀請家人加入時，
message handler 直接回分享卡、不進 agent。理由同貼圖（見 sticker_reply）：
答案固定是同一張卡，走完整條問答卻要多等 guardrail 與一次模型判斷
（2026-09-02 實測約 1.3＋1.4 秒）。

只攔「整句就是在要分享入口」的短句：先把全形轉半形、去掉空白與標點，再要求
整句符合。句子裡還有別的內容（「我朋友說加好友可以查藥嗎」）就交給 agent——
它有 share_care 工具，真的要分享時一樣叫得出同一張卡，不是的話也不會被這裡誤攔。
「分享位置」「推薦醫院」是院所搜尋的說法，刻意不在清單裡。
"""

from __future__ import annotations

import re
import unicodedata

# 比對前拿掉的字元：空白（含全形空白，NFKC 後變成一般空白）與常見的中英文標點。
_NOISE_RE = re.compile(r"[\s，,。．.！!？?～~、:：;；…\-—「」『』()（）]+")

_PREFIX = r"(?:我要|我想要|我想|請|幫我|給我|要怎麼|怎麼|如何)?"

_FRIEND = (
    r"(?:care)?加(?:入)?(?:care|這個帳號|官方帳號)?好友"
    r"|加(?:入)?(?:care|官方帳號)"
    r"|(?:分享|推薦)(?:給)?(?:朋友|好友|親友|別人)"
    r"|(?:分享|推薦)(?:care|這個系統|這個帳號|這個機器人|這個app|系統)"
    r"|邀請(?:朋友|好友|親友)(?:加入|來用|一起用)?"
)

_FAMILY = r"邀請家人(?:加入)?|(?:加|加入|新增)家人|加入(?:我的)?家庭"

# 空白已先去掉，「QR code」到這裡會是 qrcode。
_QR = r"(?:care的?|加好友的?)?(?:qrcode|行動條碼)"

_SUFFIX = r"(?:的?(?:連結|qrcode|方法|方式))?(?:嗎|呢|啊)?"

_SHARE_RE = re.compile(rf"^{_PREFIX}(?:{_FRIEND}|{_FAMILY}|{_QR}){_SUFFIX}$")


def is_share_intent(text: str) -> bool:
    """整句就是在要分享入口：加好友、分享／推薦給朋友、QR code、邀請家人。"""
    if not text:
        return False
    normalized = _NOISE_RE.sub("", unicodedata.normalize("NFKC", text).lower())
    return bool(_SHARE_RE.match(normalized))
