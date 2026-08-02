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
