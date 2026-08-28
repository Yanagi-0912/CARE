"""
建議卡必須與 symptom_department_flex_template.json 的樣式完全一致。

為什麼要對著模板檔測而不是把期望值寫死在測試裡：
    模板是設計端唯一會去改的檔案。若期望值抄一份在測試裡，設計改了模板、程式
    沒跟上時測試仍然全綠——那正是這次線上卡片跑掉的原因。這裡改成把模板當成
    唯一事實來源，兩邊不同步就一定會失敗。

比對方式是「去掉文案後的骨架」：
    文字內容本來就會依症狀變動，不該綁定；顏色、間距、字級、圓角、邊框則必須
    逐項相同。因此比對前先把所有 text/uri/label 的值抽掉。
"""

import json
from pathlib import Path

import pytest
from linebot.v3.messaging import FlexContainer

from app.services.medical.symptom_classification.symptom_department_service import (
    RESULT_FALLBACK,
    RESULT_SUGGESTION,
    SymptomTriageResult,
)
from app.services.medical.symptom_classification.symptom_table import (
    DepartmentCandidate,
    SourceReference,
    load_source_references,
)
from resources.flex_messages.medical_messages.symptom_department_flex_message import (
    _CANDIDATE_PALETTE,
    build_symptom_department_flex,
)

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[4]
    / "resources"
    / "flex_messages"
    / "medical_messages"
    / "symptom_department_flex_template.json"
)

# 文案會依症狀變動，骨架比對時一律抽掉。
_TEXT_KEYS = {"text", "uri", "label", "altText"}

_REFERENCES = (
    SourceReference(code="V", name="甲醫院", url="https://example.com/v"),
    SourceReference(code="N", name="乙醫院", url="https://example.com/n"),
)


@pytest.fixture(scope="module")
def template() -> dict:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _skeleton(node):
    """遞迴去掉所有文案值，只留樣式與結構。"""
    if isinstance(node, dict):
        return {
            key: (None if key in _TEXT_KEYS else _skeleton(value))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_skeleton(item) for item in node]
    return node


def _candidate(name, subgroup=None, sources=3):
    return DepartmentCandidate(
        canonical=name, subgroup=subgroup, facility_count=100, source_count=sources
    )


def _suggestion(candidates):
    return SymptomTriageResult(
        kind=RESULT_SUGGESTION,
        user_input="肚子痛要掛哪一科",
        matched_term="腹痛",
        candidates=candidates,
    )


def _bubble(result, references=_REFERENCES):
    return build_symptom_department_flex(result, references=references)["contents"]


def _body_parts(bubble):
    """把 body 拆成 (標題, 候選卡列表, separator, 來源區塊)。"""
    contents = bubble["body"]["contents"]
    label, *rest = contents
    source_block = rest.pop()
    separator = rest.pop()
    return label, rest, separator, source_block


# --- 與模板逐節點比對 --------------------------------------------------------


def test_header_matches_template(template):
    bubble = _bubble(_suggestion((_candidate("內科"),)))
    assert _skeleton(bubble["header"]) == _skeleton(template["header"])


def test_bubble_shell_matches_template(template):
    bubble = _bubble(_suggestion((_candidate("內科"),)))
    for key in ("type", "size"):
        assert bubble[key] == template[key]
    body, tpl_body = bubble["body"], template["body"]
    style_keys = set(tpl_body) - {"contents"}
    assert {key: body[key] for key in style_keys} == {
        key: tpl_body[key] for key in style_keys
    }


def test_footer_matches_template(template):
    bubble = _bubble(_suggestion((_candidate("內科"),)))
    assert _skeleton(bubble["footer"]) == _skeleton(template["footer"])


def test_candidate_boxes_match_template(template):
    """三張候選卡的骨架必須與模板的三張逐一相同（含交替配色）。"""
    bubble = _bubble(
        _suggestion(
            (_candidate("內科", "胃腸肝膽"), _candidate("外科"), _candidate("婦產科"))
        )
    )
    _, boxes, _, _ = _body_parts(bubble)
    tpl_boxes = [
        node
        for node in template["body"]["contents"]
        if node.get("type") == "box" and node.get("cornerRadius") == "8px"
    ]
    assert len(boxes) == len(tpl_boxes) == 3
    for box, tpl_box in zip(boxes, tpl_boxes):
        assert _skeleton(box) == _skeleton(tpl_box)


def test_separator_matches_template(template):
    bubble = _bubble(_suggestion((_candidate("內科"),)))
    _, _, separator, _ = _body_parts(bubble)
    tpl_separator = next(
        node
        for node in template["body"]["contents"]
        if node.get("type") == "separator"
    )
    assert separator == tpl_separator


def test_source_section_matches_template(template):
    bubble = _bubble(_suggestion((_candidate("內科"),)))
    _, _, _, source_block = _body_parts(bubble)
    tpl_block = template["body"]["contents"][-1]
    assert _skeleton(source_block) == _skeleton(tpl_block)


# --- 交替配色 ---------------------------------------------------------------


def test_adjacent_candidates_use_different_colors():
    """相鄰候選卡不得同色，否則在小螢幕上會黏成一塊看不出是幾個選項。"""
    bubble = _bubble(
        _suggestion((_candidate("內科"), _candidate("外科"), _candidate("婦產科")))
    )
    _, boxes, _, _ = _body_parts(bubble)
    colors = [box["backgroundColor"] for box in boxes]
    assert all(a != b for a, b in zip(colors, colors[1:]))
    assert colors == [
        _CANDIDATE_PALETTE[0][0],
        _CANDIDATE_PALETTE[1][0],
        _CANDIDATE_PALETTE[0][0],
    ]


def test_candidate_border_pairs_with_its_background():
    """底色與邊框色必須成對，配錯會出現綠底配米色框。"""
    bubble = _bubble(
        _suggestion((_candidate("內科"), _candidate("外科"), _candidate("婦產科")))
    )
    _, boxes, _, _ = _body_parts(bubble)
    for box in boxes:
        assert (box["backgroundColor"], box["borderColor"]) in _CANDIDATE_PALETTE


# --- 來源條列 ---------------------------------------------------------------


def test_sources_are_listed_one_per_line_with_links():
    """來源必須逐條可點，不得擠成一段敘述——來源的用途是讓使用者能自己核對。"""
    bubble = _bubble(_suggestion((_candidate("內科"),)))
    _, _, _, source_block = _body_parts(bubble)
    label, *items = source_block["contents"]

    assert label["text"] == "參考來源"
    assert "action" not in label
    assert len(items) == len(_REFERENCES)
    for index, (item, reference) in enumerate(zip(items, _REFERENCES), start=1):
        assert item["text"].startswith(f"{index}. ")
        assert reference.name in item["text"]
        assert item["action"]["type"] == "uri"
        assert item["action"]["uri"] == reference.url


def test_source_section_omitted_when_references_unavailable():
    """fail-soft：來源讀不到時整段不出現，不留一個空標題。"""
    bubble = _bubble(_suggestion((_candidate("內科"),)), references=())
    contents = bubble["body"]["contents"]
    assert not any(node.get("type") == "separator" for node in contents)
    assert all("參考來源" not in json.dumps(node, ensure_ascii=False) for node in contents)


def test_real_reference_file_provides_sources():
    """對照表檔真的讀得出來源，否則線上卡片會少一整段而測試仍全綠。"""
    references = load_source_references()
    assert len(references) >= 3
    assert all(ref.url.startswith("http") for ref in references)


# --- 保底卡沿用同一套樣式 ----------------------------------------------------


def test_fallback_uses_the_same_stylesheet(template):
    """保底卡只有文案不同，樣式必須與建議卡（即模板）完全一致。"""
    fallback = SymptomTriageResult(
        kind=RESULT_FALLBACK,
        user_input="全身不舒服",
        fallback_reason="這個症狀可能牽涉多個科別",
        candidates=(_candidate("家醫科", sources=0), _candidate("內科", sources=0)),
    )
    bubble = _bubble(fallback)
    assert _skeleton(bubble["header"]) == _skeleton(template["header"])
    assert _skeleton(bubble["footer"]) == _skeleton(template["footer"])
    _, boxes, _, _ = _body_parts(bubble)
    assert [box["backgroundColor"] for box in boxes] == [
        _CANDIDATE_PALETTE[0][0],
        _CANDIDATE_PALETTE[1][0],
    ]


# --- LINE 端驗證與用語邊界 ---------------------------------------------------


@pytest.mark.parametrize("count", [1, 2, 3])
def test_card_passes_line_sdk_validation(count):
    candidates = tuple(
        _candidate(name) for name in ("內科", "外科", "婦產科")[:count]
    )
    payload = _bubble(_suggestion(candidates))
    FlexContainer.from_json(json.dumps(payload, ensure_ascii=False))


def test_card_avoids_diagnostic_phrasing():
    payload = json.dumps(
        _bubble(_suggestion((_candidate("內科", "胃腸肝膽"),))), ensure_ascii=False
    )
    for forbidden in ("你應該是", "你要掛", "確診", "診斷為"):
        assert forbidden not in payload
