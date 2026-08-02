from app.services.rag.chunking import split_text_to_chunks


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
