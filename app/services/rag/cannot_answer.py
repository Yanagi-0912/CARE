CANNOT_ANSWER_MARKERS: tuple[str, ...] = (
    "不知道",
    "無法提供",
    "無法回答",
    "無法安全回答",
    "未找到",
    "找不到相關",
    "don't know",
    "do not know",
    "cannot answer",
    "unable to answer",
    "not enough information",
    "no matching",
    "わかりません",
    "答えられません",
)


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
