from app.services.rag.chunking import (
    KB_CHUNK_SIZE,
    KB_CHUNKER_VERSION,
    split_kb_chunks,
    split_text_to_chunks,
)


def test_empty_returns_empty():
    assert split_text_to_chunks("") == []
    assert split_text_to_chunks("   ") == []


def test_short_text_single_chunk():
    assert split_text_to_chunks("高血壓宜低鈉飲食。") == ["高血壓宜低鈉飲食。"]


def test_long_text_splits():
    text = ("段落A。\n\n" * 5) + ("字" * 2000)
    chunks = split_text_to_chunks(text, max_chars=500, overlap=50)
    assert len(chunks) >= 2
    assert all(c.strip() for c in chunks)


def test_no_empty_chunks():
    text = "第一段。\n\n\n\n第二段。"
    chunks = split_text_to_chunks(text)
    assert chunks == ["第一段。", "第二段。"]
    assert all(c.strip() for c in chunks)


# ── 知識庫切塊：與 CARE-data/main_pipeline.py 的 chunk_text 同一套規則 ──


def test_kb_chunker_matches_etl_version():
    """版本號與 CARE-data 的 CHUNKER_VERSION 對齊，庫裡的 chunker_version 才代表同一套切法。"""
    assert KB_CHUNKER_VERSION == 2
    assert KB_CHUNK_SIZE == 500


def test_kb_short_text_is_one_chunk():
    assert split_kb_chunks("很短的一句話。") == ["很短的一句話。"]


def test_kb_empty_input_returns_empty_list():
    assert split_kb_chunks("") == []
    assert split_kb_chunks(None) == []
    assert split_kb_chunks("   \n\n   ") == []


def test_kb_chunks_end_at_sentence_boundaries():
    text = "".join(f"這是第{i}句話，內容大約二十個字左右填充。" for i in range(1, 60))
    chunks = split_kb_chunks(text, chunk_size=200)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.rstrip().endswith(("。", "！", "？", "；", "，")), chunk[-16:]


def test_kb_no_chunk_exceeds_size():
    text = "".join(f"句子{i}。" for i in range(1, 400))
    assert all(len(c) <= 300 for c in split_kb_chunks(text, chunk_size=300))


def test_kb_default_size_splits_what_old_chunker_kept_whole():
    """約 1,200 字：舊的 split_text_to_chunks 整段收成一片，ETL 的切法要切成三片以上。"""
    text = "".join(f"第{i:03d}句衛教內容說明在這裡。" for i in range(1, 80))
    assert len(split_text_to_chunks(text)) == 1
    chunks = split_kb_chunks(text)
    assert len(chunks) >= 3
    assert all(len(c) <= KB_CHUNK_SIZE for c in chunks)


def test_kb_paragraph_boundary_preferred_over_sentence():
    para = "第一段的句子。" * 12
    text = para + "\n\n" + para
    chunks = split_kb_chunks(text, chunk_size=100)
    assert all("\n\n" not in c.strip() for c in chunks)


def test_kb_single_oversized_sentence_falls_back_to_hard_cut():
    text = "字" * 700 + "。"
    chunks = split_kb_chunks(text, chunk_size=300)
    assert len(chunks) > 1
    assert all(len(c) <= 300 for c in chunks)
    assert "".join(chunks) == text


def test_kb_no_content_is_lost():
    """不重疊、不遺失：切片接回來等於原文。"""
    text = "".join(f"第{i}句內容在這裡。" for i in range(1, 120))
    assert "".join(split_kb_chunks(text, chunk_size=250)) == text
