import pytest

from app.core.rag_sources import SourceRef
from app.services.line_messaging.flex.rag_answer_flex import (
    build_document_answer_flex,
    build_rag_answer_flex,
)
from resources.flex_messages import theme
from resources.flex_messages.theme import _SIZE_SCALE


def _sources() -> list[SourceRef]:
    return [
        SourceRef(index=1, label="食藥署", url="https://www.fda.gov.tw/b"),
        SourceRef(index=2, label="台灣 e 院", url="https://sp1.hso.mohw.gov.tw/a"),
    ]


def _text_sizes(node) -> list[str]:
    """遞迴收集 bubble 內所有 text 節點的 size。"""
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "text" and "size" in node:
            found.append(node["size"])
        for value in node.values():
            found.extend(_text_sizes(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_text_sizes(item))
    return found


def _all_text_values(node) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "text":
            found.append(node.get("text", ""))
        for value in node.values():
            found.extend(_all_text_values(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_all_text_values(item))
    return found


def _bubble_of(msg) -> dict:
    return msg.to_dict()["contents"]


@pytest.mark.parametrize("font_size", ["normal", "large", "xlarge"])
def test_every_text_size_comes_from_the_scale(font_size):
    """本次功能的核心斷言：卡片文字大小必須跟著使用者字級設定走。

    卡片內每一個 text 節點的 size 都必須是 _SIZE_SCALE 中該字級那一欄的值，
    不得出現寫死的 keyword。
    """
    ft = theme.resolve_theme(font_size)
    allowed = {sizes[font_size] for sizes in _SIZE_SCALE.values()}

    msg = build_rag_answer_flex("蜂蜜怎麼保存？", "放室溫即可 [1]。", _sources(), ft)
    sizes = _text_sizes(_bubble_of(msg))

    assert sizes, "卡片內應至少有一個帶 size 的 text 節點"
    assert set(sizes) <= allowed, (
        f"出現不屬於 {font_size} 字級的 size：{set(sizes) - allowed}"
    )


def test_larger_font_size_actually_produces_larger_keywords():
    """避免三種字級都「合法」卻其實一模一樣。"""
    normal = _text_sizes(
        _bubble_of(
            build_rag_answer_flex(
                "q", "a [1]。", _sources(), theme.resolve_theme("normal")
            )
        )
    )
    xlarge = _text_sizes(
        _bubble_of(
            build_rag_answer_flex(
                "q", "a [1]。", _sources(), theme.resolve_theme("xlarge")
            )
        )
    )

    assert normal != xlarge


def test_source_buttons_use_uri_action_with_verbatim_url():
    ft = theme.resolve_theme("large")
    sources = _sources()

    bubble = _bubble_of(build_rag_answer_flex("q", "a [1][2]。", sources, ft))

    actions = [
        node["action"]
        for node in bubble["footer"]["contents"]
        if isinstance(node, dict) and "action" in node
    ]
    assert [a["type"] for a in actions] == ["uri", "uri"]
    assert [a["uri"] for a in actions] == [s.url for s in sources]
    assert "[1]" in str(bubble["footer"]) and "食藥署" in str(bubble["footer"])


def test_source_without_url_produces_no_button():
    """URI action 缺 uri 會被 LINE 拒收；該筆仍留在純文字清單裡。"""
    ft = theme.resolve_theme("large")
    sources = [SourceRef(index=1, label="食藥署", url="")]

    bubble = _bubble_of(build_rag_answer_flex("q", "a [1]。", sources, ft))

    assert "footer" not in bubble


def test_no_sources_means_no_footer():
    ft = theme.resolve_theme("large")

    bubble = _bubble_of(build_rag_answer_flex("q", "沒有引用的答案。", [], ft))

    assert "footer" not in bubble


def test_document_card_has_no_source_section():
    """上傳文件問答不產生來源清單，卡片不得有來源區段。"""
    ft = theme.resolve_theme("large")

    bubble = _bubble_of(
        build_document_answer_flex("這份報告說什麼？", "報告指出…", ft)
    )

    assert "footer" not in bubble
    assert "參考資料來源" not in str(bubble)


def test_blank_input_never_produces_empty_text_node():
    """空字串會讓 LINE 以 400 拒收整則訊息，每個 text 節點都必須有內容。"""
    ft = theme.resolve_theme("large")

    bubble = _bubble_of(build_rag_answer_flex("", "", [], ft))

    values = _all_text_values(bubble)
    assert values, "卡片內應至少有一個 text 節點"
    assert all(v.strip() for v in values), f"出現空的 text 節點：{values}"


def test_alt_text_is_capped():
    """LINE altText 上限 400 字元，超過整則訊息會被拒收。"""
    ft = theme.resolve_theme("large")

    msg = build_rag_answer_flex("q", "衛" * 2000, [], ft)

    assert len(msg.alt_text) <= 400
