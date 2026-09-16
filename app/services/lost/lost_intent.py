"""「我走丟了」「傳位置給家人」的關鍵字判斷。

message handler 看到這兩種說法就直接啟動走失通報、不進 agent。不交給 agent 的
理由比分享卡更硬：
- guardrail 只收健康相關的問題，「我迷路了」不是健康問題，會被婉拒。
- 走丟的長輩正在慌，多等 guardrail 加一次模型判斷的幾秒是實際的代價。

兩種意圖分開回傳，因為家人收到的第一句話不同：「說他走丟了」與「想讓你知道他
在哪裡」對家人是兩種緊急程度，混成一種會讓單純分享位置的人把家人嚇壞。

誤判的兩個方向代價不同，比對規則照這個取捨：
- 漏判：長輩說的話進 agent，拿到的是一般回覆，沒有人被通知。長輩還可以換個
  說法再講一次。
- 誤判：家人收到一則「說他走丟了」。卡片會逐字附上原話，家人看得出是誤會，
  但仍是一次驚嚇。
所以「如果走失怎麼辦」「怎麼預防失智長輩走失」「我媽走丟了」這類在問別人、
問假設的句子一律不攔，交給 agent。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal, Optional

LostIntent = Literal["lost", "share"]

# 比對前拿掉的字元：空白與常見的中英文標點（同 share_intent）。
_NOISE_RE = re.compile(r"[\s，,。．.！!？?～~、:：;；…\-—「」『』()（）]+")

# 句子帶這些詞時是在問假設、問照護方法，不是本人此刻走丟了。
_HYPOTHETICAL_RE = re.compile(
    r"如果|假如|要是|萬一|預防|防止|避免|會不會|容易|常常|經常|手環|失智"
)

# 句首帶口氣詞的求救：「救命我迷路了」「糟糕走丟了」「誒誒我不知道我人在哪裡」。
# 語助詞是語音轉文字最常多出來的東西，少一個就整句比對不到（2026-09-16 實測
# 「誒誒」開頭漏判）。規則認不得的講法由走失分類器補（lost_classifier）。
_LEAD = (
    r"(?:救命|幫幫我|幫我|怎麼辦|糟糕|慘了|請問|那個|不好意思|你好|哈囉|喂"
    r"|誒|欸|唉|哎|哎呀|哎喲|啊|阿|嗯|呃|齁|厚|吼|那|就是)*"
)
# 句尾：語氣詞加上求救。「迷路了怎麼辦」仍是本人在求救。
_TAIL = r"(?:了|啦|啊|呀|耶|欸)*(?:怎麼辦|救命|幫幫我|幫我|快來|快點)*(?:啦|啊|呀|耶)*$"

_LOST_VERB = (
    r"(?:走丟|走失|迷路|迷失方向|找不到路|找不到回家的路|找不到路回家"
    r"|不知道怎麼回家|不知道回家的路|回不了家|回不去家"
    # 台語用字（語音選台語時轉出來的文字、長輩自己打的字）
    r"|揣無路|揣嘸路|毋知路|袂記得路)"
)

# 第一人稱：「我好像迷路了」「我現在找不到路回家」。主詞「我」必須緊接著
# 動詞（中間只容許副詞），「我媽走丟了」「我朋友迷路」才不會被當成本人。
_FIRST_PERSON_LOST = re.compile(
    rf"我(?:現在|好像|應該|可能|又|真的|已經|是不是|一個人|自己)*{_LOST_VERB}"
)

# 沒有主詞、整句就是在求救：「迷路了」「走丟了怎麼辦」。
_BARE_LOST = re.compile(rf"^{_LEAD}{_LOST_VERB}{_TAIL}")

# 不知道自己在哪：「我不知道我在哪裡」「這裡是哪裡」。要求整句到此結束——
# 「不知道在哪裡看醫生」「不知道診所在哪」後面還有別的內容或主詞不是自己。
# 「那裡」是長輩常打的錯字（哪／那）。
_WHERE = r"(?:哪裡|哪裏|哪邊|哪兒|哪|那裡|那裏|那邊|什麼地方|佗位|佗)"
_WHERE_AM_I = re.compile(
    rf"^{_LEAD}我?(?:現在)?(?:不知道|不曉得|搞不清楚|認不出|毋知影|毋知)(?:我|自己)?(?:現在)?(?:人)?(?:在|佇){_WHERE}{_TAIL}"
    rf"|^{_LEAD}(?:這裡|這邊|這)是{_WHERE}{_TAIL}"
)

_FAMILY = (
    r"(?:我的|我)?(?:家人|家裡|家屬|兒子|女兒|小孩|孩子|老公|老婆|先生|太太"
    r"|孫子|孫女|媳婦|女婿|哥哥|姊姊|姐姐|弟弟|妹妹)"
)

# 要把位置給家人：「傳位置給我女兒」「讓家人知道我在哪」。
_SHARE_TO_FAMILY = re.compile(
    rf"(?:傳|分享|發|送|告訴|給|報)(?:我的)?(?:位置|定位|所在地|地點)(?:給|讓){_FAMILY}"
    rf"|(?:位置|定位)(?:分享|傳|發|送)給{_FAMILY}"
    rf"|(?:讓|告訴|通知){_FAMILY}(?:知道)?我(?:現在)?在{_WHERE}"
)

# 其他語言：只收「整句就是我迷路了」的短句，長句與各種說法交給分類器
# （lost_classifier）。短句是分類器最弱的地方：字元片段太少，實測
# 「ฉันหลงทาง」「迷子になった」只落在沒把握的區間。比對前已去掉空白與標點，
# 所以 "I'm lost" 在這裡是 i'mlost。
_FOREIGN_LEAD = r"(?:help|pleasehelp|tolong|giúptôivới|ช่วยด้วย|すみません|助けて)*"
_FOREIGN_TAIL = (
    r"(?:help|please|helpme|tolong|dong|nih|rồi|ạ|giúpvới|ครับ|ค่ะ|คะ|นะ|แล้ว"
    r"|です|でした|よ|ました|助けて)*$"
)
_FOREIGN_LOST = re.compile(
    rf"^{_FOREIGN_LEAD}(?:"
    r"i'?mlost|iamlost|igotlost|i'?vegotlost|idon'?tknowwhereiam|whereami"
    r"|sayatersesat|akutersesat|sayanyasar|akunyasar|sayatidaktahusayadimana"
    r"|tôibịlạc|embịlạc|contbịlạc|tôikhôngbiếttôiđangởđâu|tôiđangởđâu"
    r"|ฉันหลงทาง|หนูหลงทาง|หลงทาง|ไม่รู้ว่าอยู่ที่ไหน"
    r"|迷子になった|迷子になりました|迷子です|迷子|道に迷った|道に迷いました|ここはどこ"
    rf"){_FOREIGN_TAIL}"
)

# 分享的句子帶這些詞是在問功能怎麼用，不是此刻要分享。求救的句子不套用：
# 「迷路了怎麼辦」是真的在求救。
_HOW_TO_RE = re.compile(r"怎麼用|怎樣|如何|怎麼傳|怎麼分享|怎麼設定|教我|功能|可不可以用")


def _normalize(text: str) -> str:
    return _NOISE_RE.sub("", unicodedata.normalize("NFKC", text).lower())


def detect_lost_intent(text: str) -> Optional[LostIntent]:
    """回傳 "lost"（本人走丟了）、"share"（要把位置給家人），或 None。"""
    if not text:
        return None
    normalized = _normalize(text)
    if not normalized or _HYPOTHETICAL_RE.search(normalized):
        return None
    if (
        _FIRST_PERSON_LOST.search(normalized)
        or _BARE_LOST.match(normalized)
        or _WHERE_AM_I.match(normalized)
        or _FOREIGN_LOST.match(normalized)
    ):
        return "lost"
    if _SHARE_TO_FAMILY.search(normalized) and not _HOW_TO_RE.search(normalized):
        return "share"
    return None
