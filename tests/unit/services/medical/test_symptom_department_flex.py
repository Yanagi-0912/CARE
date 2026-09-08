"""
建議卡必須與 symptom_department_flex_template.json 的樣式完全一致。

為什麼要對著模板檔測而不是把期望值寫死在測試裡：
    模板是設計端唯一會去改的檔案。若期望值抄一份在測試裡，設計改了模板、程式
    沒跟上時測試仍然全綠——那正是這次線上卡片跑掉的原因。這裡改成把模板當成
    唯一事實來源，兩邊不同步就一定會失敗。

比對方式是「去掉文案與字級後的骨架」：
    文字內容依症狀變動、字級依 UserSettings.font_size 變動，兩者都不該綁定；
    顏色、間距、圓角、邊框、結構則必須與模板逐項相同。

    為什麼 size 不比對：模板是手繪的設計稿，它的字級（lg／3xl／xl／md／sm）
    本來就不對應 theme 的任何單一檔次——設計時不會照著三檔 scale 畫。硬要比
    只能二選一：放棄字級設定，或把模板改成某一檔的產物而失去設計稿的身分。
    改成「模板管顏色與間距、theme 管字級」，兩邊各自有測試守著
    （見下方 test_font_size_setting_scales_the_card）。
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

# 骨架比對時一律抽掉的欄位：文案依症狀變動，size 依使用者字級變動。
_TEXT_KEYS = {"text", "uri", "label", "altText", "size"}

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


# 模板以預設字級產生；比對時必須指定同一檔，否則測的是「跑測試的人剛好
# 套用了哪一檔字級」。
TEMPLATE_FONT_SIZE = "large"


def _bubble(result, references=_REFERENCES, font_size=TEMPLATE_FONT_SIZE):
    return build_symptom_department_flex(
        result, references=references, font_size=font_size
    )["contents"]


def _body_parts(bubble):
    """把 body 拆成 (標題, 候選卡列表, 追問, separator, 來源區塊)。

    來源區塊可能不存在（讀不到來源檔時整段省略），因此從尾端拆時要先確認
    最後一節是不是來源區塊，不能無條件 pop。
    """
    contents = list(bubble["body"]["contents"])
    label, *rest = contents
    source_block = rest.pop() if rest and rest[-1].get("type") == "box" and not rest[
        -1
    ].get("cornerRadius") else None
    separator = rest.pop() if rest and rest[-1].get("type") == "separator" else None
    prompt = rest.pop()
    return label, rest, prompt, separator, source_block


# --- 與模板逐節點比對 --------------------------------------------------------


def test_header_matches_template(template):
    bubble = _bubble(_suggestion((_candidate("內科"),)))
    assert _skeleton(bubble["header"]) == _skeleton(template["header"])


def test_bubble_shell_matches_template(template):
    bubble = _bubble(_suggestion((_candidate("內科"),)))
    # bubble 的 size 是版面寬度（mega）不是字級，這裡要比。
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
    _, boxes, _, _, _ = _body_parts(bubble)
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
    _, _, _, separator, _ = _body_parts(bubble)
    tpl_separator = next(
        node
        for node in template["body"]["contents"]
        if node.get("type") == "separator"
    )
    assert separator == tpl_separator


def test_source_section_matches_template(template):
    bubble = _bubble(_suggestion((_candidate("內科"),)))
    _, _, _, _, source_block = _body_parts(bubble)
    tpl_block = template["body"]["contents"][-1]
    assert _skeleton(source_block) == _skeleton(tpl_block)


# --- 交替配色 ---------------------------------------------------------------


def test_adjacent_candidates_use_different_colors():
    """相鄰候選卡不得同色，否則在小螢幕上會黏成一塊看不出是幾個選項。"""
    bubble = _bubble(
        _suggestion((_candidate("內科"), _candidate("外科"), _candidate("婦產科")))
    )
    _, boxes, _, _, _ = _body_parts(bubble)
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
    _, boxes, _, _, _ = _body_parts(bubble)
    for box in boxes:
        assert (box["backgroundColor"], box["borderColor"]) in _CANDIDATE_PALETTE


# --- 來源條列 ---------------------------------------------------------------


def test_sources_are_listed_one_per_line_with_links():
    """來源必須逐條可點，不得擠成一段敘述——來源的用途是讓使用者能自己核對。"""
    bubble = _bubble(_suggestion((_candidate("內科"),)))
    _, _, _, _, source_block = _body_parts(bubble)
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
    _, boxes, _, _, _ = _body_parts(bubble)
    assert [box["backgroundColor"] for box in boxes] == [
        _CANDIDATE_PALETTE[0][0],
        _CANDIDATE_PALETTE[1][0],
    ]


# --- LINE 端驗證與用語邊界 ---------------------------------------------------


@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("font_size", ["normal", "large", "xlarge"])
def test_card_passes_line_sdk_validation(count, font_size):
    candidates = tuple(
        _candidate(name) for name in ("內科", "外科", "婦產科")[:count]
    )
    payload = _bubble(_suggestion(candidates), font_size=font_size)
    FlexContainer.from_json(json.dumps(payload, ensure_ascii=False))


def test_card_avoids_diagnostic_phrasing():
    payload = json.dumps(
        _bubble(_suggestion((_candidate("內科", "胃腸肝膽"),))), ensure_ascii=False
    )
    for forbidden in ("你應該是", "你要掛", "確診", "診斷為"):
        assert forbidden not in payload


# --- 字級 --------------------------------------------------------------------


@pytest.mark.parametrize(
    "font_size, expected_primary",
    [("normal", "xl"), ("large", "3xl"), ("xlarge", "4xl")],
)
def test_font_size_setting_scales_the_card(font_size, expected_primary):
    bubble = _bubble(_suggestion((_candidate("內科"),)), font_size=font_size)
    primary = bubble["header"]["contents"][1]["contents"][0]
    assert primary["size"] == expected_primary


def test_colors_and_spacing_do_not_move_with_font_size(template):
    """
    字級只該影響 size。顏色、間距、圓角仍以模板為準——兩者混在一起改動時，
    「版面跑掉」會很難查是字級還是樣式造成的。
    """

    def without_sizes(node):
        if isinstance(node, dict):
            return {
                key: (None if key == "size" else without_sizes(value))
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [without_sizes(item) for item in node]
        return node

    result = _suggestion(
        (_candidate("內科", "胃腸肝膽"), _candidate("外科"), _candidate("婦產科"))
    )
    baseline = without_sizes(_skeleton(_bubble(result, font_size="large")))
    for font_size in ("normal", "xlarge"):
        assert (
            without_sizes(_skeleton(_bubble(result, font_size=font_size))) == baseline
        )


# --- 追問與 Quick Reply ------------------------------------------------------
#
# 追問的用途是把「知道要掛哪一科」接到「找得到附近哪裡有」。按鈕送出的是明確
# 語句而不是「好」——後者需要跨輪的待確認狀態，而且使用者可能在回應別的事。


def test_prompt_names_the_first_ranked_department():
    """
    追問裡的科別必須是第一順位那一個，不是隨便一個候選——那是卡片給出的
    主要建議，也是按鈕會去搜尋的目標，兩者不一致會讓使用者搜到別的科。
    """
    bubble = _bubble(
        _suggestion((_candidate("皮膚科"), _candidate("神經科"), _candidate("家醫科")))
    )
    _, _, prompt, _, _ = _body_parts(bubble)
    assert "皮膚科" in prompt["text"]
    assert "神經科" not in prompt["text"] and "家醫科" not in prompt["text"]


def test_prompt_sits_between_candidates_and_sources():
    """位置是刻意的：看完科別才問要不要找院所，來源則留在最後不打斷這個動線。"""
    contents = _bubble(_suggestion((_candidate("內科"),)))["body"]["contents"]
    types = [node.get("type") for node in contents]
    prompt_index = next(
        i
        for i, node in enumerate(contents)
        if node.get("type") == "text" and "是否需要搜尋附近" in node.get("text", "")
    )
    last_candidate = max(
        i for i, node in enumerate(contents) if node.get("cornerRadius") == "8px"
    )
    assert last_candidate < prompt_index < types.index("separator")


def test_quick_reply_sends_an_explicit_sentence_not_a_yes():
    """
    送「好」需要 agent 記得剛剛問了什麼；送「搜尋附近的皮膚科」不需要，
    而且既有的 _is_nearby_department_intent() 直接接得住。
    """
    payload = build_symptom_department_flex(
        _suggestion((_candidate("皮膚科"),)), references=()
    )
    action = payload["quickReply"]["items"][0]["action"]
    assert action["type"] == "message"
    assert action["text"] == "搜尋附近的皮膚科"
    assert action["text"] not in ("好", "是", "要")


def test_quick_reply_text_is_routable_by_the_existing_nearby_intent():
    """按鈕文字必須被既有路由接住，否則點了等於沒反應。"""
    from app.services.agent.utils.nodes import _is_nearby_department_intent
    from app.services.medical.department_matcher import extract_department_intent

    for name in ("皮膚科", "內科", "眼科", "家醫科"):
        payload = build_symptom_department_flex(
            _suggestion((_candidate(name),)), references=()
        )
        text = payload["quickReply"]["items"][0]["action"]["text"]
        assert _is_nearby_department_intent(text) is True
        match = extract_department_intent(text)
        assert match is not None and match.canonical == name


def test_quick_reply_label_fits_line_limit():
    """LINE 的 Quick Reply label 上限 20 字，超過會被拒絕整則訊息。"""
    payload = build_symptom_department_flex(
        _suggestion((_candidate("職業醫學科"),)), references=()
    )
    assert len(payload["quickReply"]["items"][0]["action"]["label"]) <= 20


def test_fallback_card_offers_its_own_primary_department():
    """保底卡的第一順位是家醫科，追問與按鈕都要跟著它，不能沿用建議卡的科別。"""
    fallback = SymptomTriageResult(
        kind=RESULT_FALLBACK,
        user_input="全身不舒服",
        fallback_reason="無法對應到已知的症狀條目",
        candidates=(_candidate("家醫科", sources=0), _candidate("內科", sources=0)),
    )
    payload = build_symptom_department_flex(fallback, references=())
    assert "家醫科" in payload["quickReply"]["items"][0]["action"]["text"]


def test_quick_reply_survives_the_reply_path():
    """
    卡片以純 dict 描述按鈕，SDK 需要物件。這段轉換先前不存在，quickReply
    會被無聲丟掉——按鈕不出現且沒有任何錯誤訊息。
    """
    import json

    from app.services.line_messaging.reply.reply import LineReplier

    payload = build_symptom_department_flex(
        _suggestion((_candidate("皮膚科"),)), references=()
    )
    message = LineReplier._try_parse_flex_message(json.dumps(payload, ensure_ascii=False))
    assert message is not None
    assert message.quick_reply is not None
    assert message.quick_reply.items[0].action.text == "搜尋附近的皮膚科"


def test_malformed_quick_reply_does_not_break_the_card():
    """少一顆按鈕遠好過整張卡片退化成純文字。"""
    from app.services.line_messaging.reply.reply import LineReplier

    for bad in (None, {}, {"items": []}, {"items": [{"action": {"type": "postback"}}]}):
        assert LineReplier._parse_quick_reply(bad) is None
