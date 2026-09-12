"""判斷模型是否答不出來。

只認 prompt 要求模型寫出的固定標記，不比對「不知道」「無法提供」這類字眼——
理由與實測數字見 answer_prompts._NO_ANSWER_RULE。字眼比對兩頭都會錯：模型換
個說法或換個語言就漏抓；正常回答裡剛好出現「不知道」「無法提供」，又會整段
被丟掉。
"""

NO_ANSWER_SENTINEL = "[NO_ANSWER]"

# 名稱與 tuple 形式保留：answer_service、web_search_service 的拒答判斷，以及
# eval_scoring.is_refuse_ok 都透過它比對。
CANNOT_ANSWER_MARKERS: tuple[str, ...] = (NO_ANSWER_SENTINEL,)


def matched_cannot_answer_marker(text: str, markers: tuple[str, ...]) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return "<empty>"
    for marker in markers:
        if marker in normalized:
            return marker
    return "<none>"


def answer_preview(text: str, limit: int = 200) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit]
