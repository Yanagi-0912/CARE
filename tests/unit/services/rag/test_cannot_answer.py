import pytest

from app.services.rag.cannot_answer import (
    CANNOT_ANSWER_MARKERS,
    NO_ANSWER_SENTINEL,
    answer_preview,
    matched_cannot_answer_marker,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "<empty>"),
        ("   ", "<empty>"),
        (None, "<empty>"),
        (f"{NO_ANSWER_SENTINEL}\n找不到相關資料。", NO_ANSWER_SENTINEL),
        (f"{NO_ANSWER_SENTINEL} Không tìm thấy thông tin liên quan.", NO_ANSWER_SENTINEL),
        (
            "河魨毒素結構穩定，無法透過加熱破壞，請勿自行處理。",
            "<none>",
        ),
        ("正常可回答的衛教內容", "<none>"),
        # 以下是舊的字眼比對會判成拒答的正常回答，現在都不算：
        ("很多人不知道自己有高血壓，建議定期量血壓 [1]。", "<none>"),
        (
            "PGAD 是一種罕見疾病 [1]。資料沒有提到治療方式，無法提供更進一步的說明。",
            "<none>",
        ),
    ],
)
def test_matched_cannot_answer_marker(text, expected):
    assert matched_cannot_answer_marker(text, CANNOT_ANSWER_MARKERS) == expected


def test_refusal_is_detected_only_by_sentinel():
    """拒答只認標記，不再比對字眼（理由見 answer_prompts._NO_ANSWER_RULE）。"""
    assert CANNOT_ANSWER_MARKERS == (NO_ANSWER_SENTINEL,)


def test_answer_preview_collapses_whitespace_and_truncates():
    text = "  第一行\n\n第二行   " + "x" * 300
    preview = answer_preview(text, limit=200)
    prefix = "第一行 第二行 "
    assert preview == prefix + "x" * (200 - len(prefix))
    assert len(preview) == 200


def test_answer_preview_empty():
    assert answer_preview("") == ""
    assert answer_preview("   ") == ""
