import pytest

from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import all_rag_prefixes, strip_rag_prefix, t


def test_all_rag_prefixes_covers_every_supported_language():
    prefixes = all_rag_prefixes()
    for lang in SUPPORTED_LANGUAGES:
        assert t("agent.rag_prefix", lang) in prefixes


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_strip_removes_prefix_for_every_language(lang):
    prefix = t("agent.rag_prefix", lang)

    assert strip_rag_prefix(f"{prefix}\n蜂蜜放室溫即可。") == "蜂蜜放室溫即可。"


def test_strip_is_noop_without_prefix():
    text = "蜂蜜放室溫即可。"
    assert strip_rag_prefix(text) == text


def test_strip_only_removes_leading_occurrence():
    """前綴出現在句中時不得刪除——那是答案內容的一部分。"""
    text = "使用者問：以下為 RAG 回應：是什麼意思？"
    assert strip_rag_prefix(text) == text


def test_strip_preserves_answer_body_whitespace_structure():
    prefix = t("agent.rag_prefix", "zh-TW")
    text = f"{prefix}\n\n第一段。\n\n第二段。"

    assert strip_rag_prefix(text) == "第一段。\n\n第二段。"
