"""文字切塊。兩套切法，服務兩個不同的庫：

- `split_kb_chunks`：知識庫（health_articles_chunks）。與 CARE-data/main_pipeline.py
  的 `chunk_text` 是同一套規則，**兩邊要一起改**。庫裡的 chunk 幾乎全是 ETL 切的，
  這裡若另用一套規則，同一個索引裡就有兩種片段長度與斷點，向量與 BM25 的分數
  也就不在同一個基準上比。
- `split_text_to_chunks`：使用者上傳文件（另一個 collection、另一條沒有精排的
  檢索路徑），維持原本的段落／1200 字切法。
"""

# 與 CARE-data/main_pipeline.py 的 CHUNKER_VERSION 同步。每個 chunk 都寫這個欄位，
# ETL 靠它判斷舊切法的文章要不要重切。
KB_CHUNKER_VERSION = 2
# 沿用 ETL 的值，理由同上：與庫裡既有的 chunk 一致。
KB_CHUNK_SIZE = 500
_KB_SEPARATORS = ("\n\n", "\n", "。", "！", "？", "；", "，")


def _split_keeping_separator(text: str, separator: str) -> list[str]:
    """以 separator 切開，但把 separator 留在前一段的尾端。"""
    parts = text.split(separator)
    out = [p + separator for p in parts[:-1]]
    if parts[-1]:
        out.append(parts[-1])
    return out


def _recursive_split(
    text: str, chunk_size: int, separators: tuple[str, ...]
) -> list[str]:
    """逐層退讓的切分：先試最粗的分隔，切不夠小才往下一層。

    最後一層是字元硬切，因為單一句子也可能超過 chunk_size（ETL 線上實測最長
    的一句有 200 字以上）。那時硬切是唯一選擇，但已經是罕例而非常態。
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    for index, separator in enumerate(separators):
        if separator not in text:
            continue
        pieces = _split_keeping_separator(text, separator)
        # 這一層切不動（例如整段只有一個分隔且在結尾），換下一層
        if len(pieces) <= 1:
            continue

        chunks: list[str] = []
        buffer = ""
        for piece in pieces:
            if len(buffer) + len(piece) <= chunk_size:
                buffer += piece
                continue
            if buffer:
                chunks.append(buffer)
            # 單一片段仍超長時，用更細的分隔再切一次
            if len(piece) > chunk_size:
                chunks.extend(
                    _recursive_split(piece, chunk_size, separators[index + 1 :])
                )
                buffer = ""
            else:
                buffer = piece
        if buffer:
            chunks.append(buffer)
        return [c for c in chunks if c.strip()]

    # 所有分隔都用盡：硬切
    return [
        text[i : i + chunk_size]
        for i in range(0, len(text), chunk_size)
        if text[i : i + chunk_size].strip()
    ]


def split_kb_chunks(text: str | None, *, chunk_size: int = KB_CHUNK_SIZE) -> list[str]:
    """知識庫切塊：不超過 chunk_size，盡量在段落、句子、子句的邊界斷開。

    不重疊：邊界感知的切分本來就在完整句子處斷開，重疊原本要解決的「句子被
    切斷」已經不存在（ETL 那邊的註解引了 2026 年 1 月的 arXiv 分析）。
    """
    if not text:
        return []
    return _recursive_split(text, chunk_size, _KB_SEPARATORS)


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
