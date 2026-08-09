from app.core.user_language import reset_request_language, set_request_language
from app.services.rag.answer_prompts import build_rag_prompt, build_web_prompt


def test_rag_prompt_requires_citation_markers():
    template = build_rag_prompt("zh-TW")
    text = template.format_messages(question="q", context="c")[0].content
    assert "每一項資訊都必須標上來源編號" in text
    assert "沒有任何一段內容支持的敘述，不要寫入回答" in text


def test_build_rag_prompt_requires_english_when_request_language_en():
    token = set_request_language("en")
    try:
        text = build_rag_prompt().format_messages(question="q", context="c")[0].content
        assert "English" in text
        assert "翻譯" in text or "改寫" in text
    finally:
        reset_request_language(token)


def test_build_web_prompt_requires_japanese_when_language_ja():
    text = build_web_prompt("ja").format_messages(question="q", context="c")[0].content
    assert "日本語" in text
