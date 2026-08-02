import pytest

from app.services.rag.cannot_answer import (
    CANNOT_ANSWER_MARKERS,
    answer_preview,
    matched_cannot_answer_marker,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "<empty>"),
        ("   ", "<empty>"),
        (None, "<empty>"),
        ("根據現有資料無法提供建議。", "無法提供"),
        ("我不知道", "不知道"),
        (
            "河魨毒素結構穩定，無法透過加熱破壞，請勿自行處理。",
            "<none>",
        ),
        ("正常可回答的衛教內容", "<none>"),
        ("I don't know the answer.", "don't know"),
    ],
)
def test_matched_cannot_answer_marker(text, expected):
    assert matched_cannot_answer_marker(text, CANNOT_ANSWER_MARKERS) == expected


def test_answer_preview_collapses_whitespace_and_truncates():
    text = "  第一行\n\n第二行   " + "x" * 300
    preview = answer_preview(text, limit=200)
    prefix = "第一行 第二行 "
    assert preview == prefix + "x" * (200 - len(prefix))
    assert len(preview) == 200


def test_answer_preview_empty():
    assert answer_preview("") == ""
    assert answer_preview("   ") == ""
