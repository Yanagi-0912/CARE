import json

import pytest

from app.services.gemini.shared.errors import GeminiParseError
from app.services.gemini.shared.parser import content_to_text, parse_json_from_model_text


def test_parse_plain_json_object():
    out = parse_json_from_model_text('{"is_health_related": true}')
    assert out == {"is_health_related": True}


def test_parse_json_in_markdown_fence():
    raw = """```json
{"is_health_related": false}
```"""
    out = parse_json_from_model_text(raw)
    assert out == {"is_health_related": False}


def test_parse_json_fence_without_lang():
    raw = """```
{"foo": 1}
```"""
    out = parse_json_from_model_text(raw)
    assert out == {"foo": 1}


def test_parse_raises_on_invalid_json():
    with pytest.raises(json.JSONDecodeError):
        parse_json_from_model_text("not json {}")


def test_parse_raises_when_root_is_not_object():
    with pytest.raises(GeminiParseError, match="不是物件"):
        parse_json_from_model_text("[1,2,3]")


# ── content_to_text：攤平 ChatModel .content（次要 finding 1，見
# app/services/agent/agent.py 與 claim_verification/service.py 的共用需求）──


def test_content_to_text_returns_plain_string_unchanged():
    assert content_to_text("純文字回應") == "純文字回應"


def test_content_to_text_returns_empty_string_for_none():
    assert content_to_text(None) == ""


def test_content_to_text_flattens_list_of_text_parts():
    content = [{"type": "text", "text": "第一段。"}, {"type": "text", "text": "第二段。"}]
    assert content_to_text(content) == "第一段。第二段。"


def test_content_to_text_flattens_mixed_list_of_str_and_dict_parts():
    content = ["前綴：", {"type": "text", "text": "主要內容"}]
    assert content_to_text(content) == "前綴：主要內容"


def test_content_to_text_does_not_leak_python_repr_for_list_of_parts():
    """核心規格：list-of-parts 不能被 str() 整包印成 Python repr（含引號、
    大括號、type 這類內部鍵名），那些符號不該出現在最終文字裡。"""
    content = [{"type": "text", "text": "查核報告內容"}]
    result = content_to_text(content)
    assert "'type'" not in result
    assert "{" not in result


def test_content_to_text_stringifies_non_text_dict_part_without_text_key():
    content = [{"type": "image_url", "url": "https://example.com/x.png"}]
    assert content_to_text(content) == ""
