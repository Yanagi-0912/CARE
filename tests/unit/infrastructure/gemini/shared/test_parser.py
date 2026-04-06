import json

import pytest

from app.infrastructure.gemini.shared.errors import GeminiParseError
from app.infrastructure.gemini.shared.parser import parse_json_from_model_text


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
