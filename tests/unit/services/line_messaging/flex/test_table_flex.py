"""表格卡的組裝測試。

測資直接取自 n8n 影像解析節點的實際輸出（gemini-3.8-flash 對手寫表格的回傳），
不是自己編的漂亮格式——手寫辨識結果本來就會出現空格、缺欄與括號註記。
"""

import pytest

from app.services.line_messaging.flex.table_flex import (
    _EMPTY_CELL,
    build_table_flex,
    build_table_flex_from_text,
    parse_markdown_table,
)
from resources.flex_messages import size_guard, theme
from resources.flex_messages.theme import _SIZE_SCALE

# n8n 實際輸出：同上箭頭已被展開成「值（註記）」
DITTO_TEXT = """吃藥紀錄

| 日期 | 早 | 中 | 晚 |
| --- | --- | --- | --- |
| 9/1 | ✓ | ✓ | ✓ |
| 9/2 | ✓（↓同上） | ✓（↓同上） | ✓（↓同上） |
| 9/3 | ✓ | ✕ | ✓ |"""

# n8n 實際輸出：空儲存格（那一餐沒有紀錄）
BLANK_TEXT = """吃藥紀錄

| 日期 | 早 | 中 | 晚 |
| --- | --- | --- | --- |
| 9/1 | ✓ | ✓ | ✕ |
| 9/2 | ✓ |  | ✓ |"""

TALLY_TEXT = """散步次數

| 週次 | 次數 |
| --- | --- |
| 第二週 | 正正丅（12 次） |
| 第三週 | 正一（6 次） |"""


def _nodes(node, kind: str) -> list:
    found = []
    if isinstance(node, dict):
        if node.get("type") == kind:
            found.append(node)
        for value in node.values():
            found.extend(_nodes(value, kind))
    elif isinstance(node, list):
        for item in node:
            found.extend(_nodes(item, kind))
    return found


def _texts(bubble) -> list[str]:
    return [n.get("text", "") for n in _nodes(bubble, "text")]


def _bubble(message) -> dict:
    return message.contents.to_dict()


def _theme() -> theme.FlexTheme:
    return theme.resolve_theme("large")


class TestParse:
    def test_extracts_title_columns_and_rows(self):
        preamble, columns, rows = parse_markdown_table(DITTO_TEXT)
        assert preamble == ["吃藥紀錄"]
        assert columns == ["日期", "早", "中", "晚"]
        assert len(rows) == 3
        assert rows[1] == ["9/2", "✓（↓同上）", "✓（↓同上）", "✓（↓同上）"]

    def test_keeps_estimate_notice_as_separate_preamble_line(self):
        text = "體重紀錄\n（數值為推估，僅供參考）\n\n| 日期 | 體重 |\n| --- | --- |\n| 9/1 | 62 |"
        preamble, _, _ = parse_markdown_table(text)
        assert preamble == ["體重紀錄", "（數值為推估，僅供參考）"]

    def test_pads_short_rows_instead_of_discarding(self):
        text = "x\n\n| a | b | c |\n| --- | --- | --- |\n| 1 | 2 |"
        _, columns, rows = parse_markdown_table(text)
        assert len(rows[0]) == len(columns) == 3

    def test_returns_none_without_table(self):
        assert parse_markdown_table("今天血壓 138/82，還好。") is None

    def test_returns_none_when_header_has_no_body(self):
        assert parse_markdown_table("t\n\n| a | b |\n| --- | --- |") is None


class TestCellNotes:
    def test_note_is_split_out_and_kept(self):
        bubble = _bubble(build_table_flex_from_text(DITTO_TEXT, _theme()))
        texts = _texts(bubble)
        # 主值與註記都在，且是分開的兩個 text 節點
        assert "✓" in texts
        assert "↓同上" in texts
        assert "✓（↓同上）" not in texts

    def test_tally_count_kept_as_note(self):
        bubble = _bubble(build_table_flex_from_text(TALLY_TEXT, _theme()))
        texts = _texts(bubble)
        assert "正正丅" in texts and "12 次" in texts

    def test_note_uses_smaller_faint_style(self):
        bubble = _bubble(build_table_flex_from_text(TALLY_TEXT, _theme()))
        note = next(n for n in _nodes(bubble, "text") if n["text"] == "12 次")
        value = next(n for n in _nodes(bubble, "text") if n["text"] == "正正丅")
        assert note["color"] == theme.TEXT_FAINT
        assert note["size"] != value["size"]

    def test_blank_cell_renders_as_faint_dash(self):
        bubble = _bubble(build_table_flex_from_text(BLANK_TEXT, _theme()))
        dash = [n for n in _nodes(bubble, "text") if n["text"] == _EMPTY_CELL]
        assert len(dash) == 1
        assert dash[0]["color"] == theme.TEXT_FAINT

    def test_no_empty_text_node_anywhere(self):
        """空字串會讓整則訊息被 LINE 以 400 拒收。"""
        for text in (DITTO_TEXT, BLANK_TEXT, TALLY_TEXT):
            bubble = _bubble(build_table_flex_from_text(text, _theme()))
            assert all(t != "" for t in _texts(bubble))


class TestLayout:
    def test_title_goes_to_header(self):
        bubble = _bubble(build_table_flex_from_text(DITTO_TEXT, _theme()))
        assert bubble["header"]["contents"][0]["text"] == "吃藥紀錄"

    def test_notice_rendered_in_warning_colour(self):
        text = "體重紀錄\n（數值為推估，僅供參考）\n\n| 日期 | 體重 |\n| --- | --- |\n| 9/1 | 62 |"
        bubble = _bubble(build_table_flex_from_text(text, _theme()))
        notice = next(
            n for n in _nodes(bubble, "text") if n["text"].startswith("（數值為推估")
        )
        assert notice["color"] == theme.STATUS_PENDING

    def test_wide_table_switches_to_stacked_layout(self):
        columns = ["日期", "早", "中", "晚", "睡前", "備註"]
        rows = [["9/1", "✓", "✓", "✓", "✕", "頭暈"]]
        bubble = _bubble(
            build_table_flex(columns=columns, rows=rows, ft=_theme(), title="吃藥")
        )
        # 直式時欄名與值同列出現，不會有一列六格的 horizontal box
        horizontal = [
            n for n in _nodes(bubble["body"], "box") if n.get("layout") == "horizontal"
        ]
        assert all(len(n["contents"]) == 2 for n in horizontal)
        assert "睡前" in _texts(bubble)

    def test_header_row_is_tinted(self):
        bubble = _bubble(build_table_flex_from_text(DITTO_TEXT, _theme()))
        boxes = [
            n
            for n in _nodes(bubble["body"], "box")
            if n.get("backgroundColor") == theme.BRAND_TINT
        ]
        assert len(boxes) == 1

    def test_estimate_notice_on_first_line_is_not_the_title(self):
        """n8n 的 Code 節點把推估警語加在 text 第一行（`${ESTIMATED_NOTICE}\\n${text}`）。

        照順序拿第一行當標題，抬頭會變成一句警告，真正的標題反而被排成警語。
        """
        text = "（數值為推估，僅供參考）\n體重紀錄\n\n| 日期 | 體重 |\n| --- | --- |\n| 9/1 | 62 |"
        bubble = _bubble(build_table_flex_from_text(text, _theme()))
        assert bubble["header"]["contents"][0]["text"] == "體重紀錄"
        notice = next(
            n
            for n in _nodes(bubble["body"], "text")
            if n["text"] == "（數值為推估，僅供參考）"
        )
        assert notice["color"] == theme.STATUS_PENDING

    def test_notice_without_title_stays_out_of_header(self):
        text = "（數值為推估，僅供參考）\n| 日期 | 體重 |\n| --- | --- |\n| 9/1 | 62 |"
        bubble = _bubble(build_table_flex_from_text(text, _theme()))
        assert "推估" not in bubble["header"]["contents"][0]["text"]
        assert "（數值為推估，僅供參考）" in _texts(bubble["body"])


class TestSizeAndDegradation:
    def test_long_table_is_truncated_with_a_note(self):
        columns = ["日期", "血壓", "脈搏"]
        rows = [[f"9/{i}", "138/82", "72"] for i in range(1, 200)]
        message = build_table_flex(columns=columns, rows=rows, ft=_theme(), title="血壓")
        bubble = _bubble(message)
        assert size_guard.fits(bubble)
        assert any("未顯示" in t for t in _texts(bubble))

    def test_raises_when_even_minimal_table_is_too_large(self):
        columns = ["a", "b"]
        rows = [["x" * 6000, "y" * 6000] for _ in range(3)]
        with pytest.raises(ValueError):
            build_table_flex(columns=columns, rows=rows, ft=_theme(), title="t")

    def test_from_text_returns_none_instead_of_raising(self):
        assert build_table_flex_from_text("沒有表格", _theme()) is None

    def test_alt_text_within_line_limit(self):
        columns = ["日期", "血壓", "脈搏"]
        rows = [[f"9/{i}", "138/82", "72"] for i in range(1, 100)]
        message = build_table_flex(columns=columns, rows=rows, ft=_theme(), title="血壓")
        assert 0 < len(message.alt_text) <= 400

    def test_empty_columns_rejected(self):
        with pytest.raises(ValueError):
            build_table_flex(columns=[], rows=[], ft=_theme())


class TestFontSizes:
    @pytest.mark.parametrize("scale", sorted(_SIZE_SCALE["body"]))
    def test_all_font_scales_produce_valid_card(self, scale):
        ft = theme.resolve_theme(scale)
        bubble = _bubble(build_table_flex_from_text(DITTO_TEXT, ft))
        assert size_guard.fits(bubble)
        body_size = _SIZE_SCALE["body"][scale]
        assert body_size in [n["size"] for n in _nodes(bubble, "text")]
