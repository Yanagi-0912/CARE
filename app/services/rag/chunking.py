def split_text_to_chunks(
    text: str,
    *,
    max_chars: int = 1200,
    overlap: int = 100,
) -> list[str]:
    if not text or not text.strip():
        return []

    chunks: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
            continue

        start = 0
        while start < len(paragraph):
            end = min(start + max_chars, len(paragraph))
            chunk = paragraph[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(paragraph):
                break
            start = end - overlap

    return chunks
