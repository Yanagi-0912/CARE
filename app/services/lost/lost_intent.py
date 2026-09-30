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

from app.services.medical.department_matcher import (
    CANONICAL_DEPARTMENTS,
    DEPARTMENT_ALIASES,
)

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

# ── 找院所：整句就是「幫我找附近診所」──────────────────────────────────
# 這類句子不交給走失分類器。2026-09-30「幫我找附近診所」被分類器給了 0.44，落在
# 沒把握的區間，回覆下方多出一顆「我迷路了，通知家人」；「幫我找附近醫院」是 0.67，
# 離直接通知家人的 0.70 只差一點。用模板量分類器，這種短句有兩成會附按鈕。
#
# 為什麼用規則擋、不補資料重訓：補模板短句當負例重訓了兩版，短句是壓下來了，
# holdout 的走失正例卻從「直接通報」掉到「只附按鈕」：
#   補六語 450 筆　　　短句附按鈕 19/90 → 4/90，正例直接通報 505/536 → 492
#   只補中英印尼 270 筆　短句附按鈕 15/54 → 2/54，正例直接通報 505/536 → 479
# 「找附近的院所」與「我在某某附近、找不到路」對模型來說太近，壓低前者就連帶壓低
# 後者。真的走丟的人被降級成只附按鈕，比多一顆按鈕嚴重。這條規則在走失資料集
# 20,036 筆上一筆都沒命中（正例 2,695 筆全數照舊交給分類器），模板短句附按鈕
# 30/150 → 1/150。只管中文；英文、印尼文的短句仍有三成多會附按鈕。
#
# 要求整句從頭到尾只有「找＋附近＋院所類別」，多一個字就不算、照舊交給分類器：
# 「我在醫院附近迷路了」「這裡是哪裡附近只有一間藥局」都比對不到。
_PLACE_LEAD = r"(?:請問|請|麻煩|不好意思|你好|哈囉|那個|可不可以|可以|能不能)*"
_PLACE_FIND = (
    r"(?:(?:幫我|幫忙|替我|給我)?(?:找|查|搜尋|搜|看|推薦)(?:一下|看看)?"
    r"|我?(?:想要|想|要)(?:找|去|看|掛))?"
)
_PLACE_NEAR = (
    r"(?:這|我家|我這|離我|離這裡)?(?:附近|最近|周邊|周圍|鄰近)?的?"
    r"(?:現在)?(?:還)?(?:有開)?的?(?:有沒有|哪裡有|哪邊有|有哪些|有什麼|有)?"
)
# 院所類別＝院所種類＋科別。科別沿用找院所工具認得的說法（department_matcher），
# 只取「…科」結尾的：別名表裡的「牙齒」「生產」「洗腎」是身體部位與處置，不是去處。
_PLACE_KINDS = (
    "診所", "小診所", "醫院", "大醫院", "醫療院所", "院所", "藥局", "藥房", "健保藥局",
    "急診", "急診室", "衛生所", "中醫", "西醫", "牙醫", "洗腎中心",
)
_PLACE_TERMS = sorted(
    {
        *_PLACE_KINDS,
        *(name for name in (*CANONICAL_DEPARTMENTS, *DEPARTMENT_ALIASES) if name.endswith("科")),
    },
    key=lambda term: (-len(term), term),
)
_PLACE = rf"(?:{'|'.join(map(re.escape, _PLACE_TERMS))})(?:診所|醫院|門診)?"
_PLACE_TAIL = r"(?:嗎|呢|好嗎|可以嗎|謝謝|感謝|拜託|有哪些|有嗎|在哪裡|在哪)*$"
_PLACE_SEARCH = re.compile(
    rf"^{_PLACE_LEAD}{_PLACE_FIND}{_PLACE_NEAR}{_PLACE}(?:(?:或是|或|和|跟|還有){_PLACE})*{_PLACE_TAIL}"
)


def _normalize(text: str) -> str:
    return _NOISE_RE.sub("", unicodedata.normalize("NFKC", text).lower())


def is_place_search(text: str) -> bool:
    """整句就是在找附近的院所（「幫我找附近診所」「附近有藥局嗎」）。"""
    return bool(text) and _PLACE_SEARCH.match(_normalize(text)) is not None


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
