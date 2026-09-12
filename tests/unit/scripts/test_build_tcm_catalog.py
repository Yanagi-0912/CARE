"""中藥庫建表的解析規則。

這支測試釘住的是「處方欄位怎麼變成可比對的藥材清單」——切錯會讓整個中藥側
的比對失效，而失效的方式是安靜的（比對不到就是沒事發生）。
"""

import pytest

from scripts.build_tcm_catalog import (
    build_entries,
    parse_formula_detail,
    parse_herbs,
    parse_item_rows,
    resolve_output_path,
)


class TestParseHerbs:
    def test_splits_and_strips_quantities(self):
        """份量刻意去掉：比對要問的是「這一方含不含麻黃」，不是含多少。
        保留份量會讓同一味藥在不同方裡變成不同字串，交集永遠是空的。"""
        assert parse_herbs("葛根6、麻黃4.5、桂枝3、白芍3、炙甘草3、生薑4.5、大棗4 (一日飲片量28公克)。") == [
            "葛根",
            "麻黃",
            "桂枝",
            "白芍",
            "炙甘草",
            "生薑",
            "大棗",
        ]

    def test_drops_preparation_notes_after_the_period(self):
        """「傳統製劑加蜂蜜適量」是製法不是藥材。"""
        assert parse_herbs(
            "熟地黃8、山茱萸4、山藥4、澤瀉3、牡丹皮3、茯苓3 (一日飲片量25公克)。傳統製劑加蜂蜜適量。"
        ) == ["熟地黃", "山茱萸", "山藥", "澤瀉", "牡丹皮", "茯苓"]

    def test_processed_prefix_is_not_merged(self):
        """炙甘草與甘草不合併——兩者藥性在中醫理論裡有別，合併是一個我們
        沒有依據的藥理判斷。分開的代價只是多列一項，遠小於猜錯。"""
        herbs = parse_herbs("甘草3、炙甘草2")
        assert herbs == ["甘草", "炙甘草"]

    def test_empty_formula_yields_nothing(self):
        assert parse_herbs("") == []


class TestParseFormulaDetail:
    _MARKUP = """
        <div><span>名稱</span><span>葛根湯</span></div>
        <div><span>出典</span><span>傷寒論</span></div>
        <div><span>處方</span><span>葛根6、麻黃4.5、桂枝3 (一日飲片量28公克)。</span></div>
        <div><span>效能</span><span>發汗解肌。</span></div>
        <div><span>適應症</span><span>外感風寒，頭痛發熱。</span></div>
    """

    def test_maps_every_field(self):
        entry = parse_formula_detail("025", self._MARKUP)

        assert entry["code"] == "P025"
        assert entry["kind"] == "formula"
        assert entry["name_zh"] == "葛根湯"
        assert entry["source"] == "傷寒論"
        assert entry["herbs"] == ["葛根", "麻黃", "桂枝"]
        assert entry["effect"] == "發汗解肌。"
        assert entry["indication"] == "外感風寒，頭痛發熱。"

    def test_page_without_a_formula_is_skipped(self):
        """沒有組成的方對比對沒有貢獻，留著只會讓下游以為它有資料。"""
        assert parse_formula_detail("999", "<div><span>名稱</span><span>某方</span></div>") is None


class TestParseItemRows:
    _MARKUP = """
        <table><tr><td>項次</td><td>中文名</td><td>拉丁生藥名稱</td><td>英文名稱</td></tr>
        <tr><td>001</td><td>丁香</td><td>CARYOPHYLLI  FLOS</td><td>Clove</td></tr>
        <tr><td>002</td><td>人參</td><td>GINSENG  RADIX</td><td>Ginseng Root</td></tr></table>
    """

    def test_maps_rows_and_collapses_latin_whitespace(self):
        entries = parse_item_rows(self._MARKUP, "herb")

        assert [e["code"] for e in entries] == ["H001", "H002"]
        assert entries[0]["name_latin"] == "CARYOPHYLLI FLOS"

    def test_single_herb_composition_is_itself(self):
        """讓下游用同一個欄位比對單味藥與複方，不必寫兩條路徑。"""
        (first, _) = parse_item_rows(self._MARKUP, "herb")
        assert first["herbs"] == ["丁香"]

    def test_header_row_is_skipped(self):
        assert len(parse_item_rows(self._MARKUP, "herb")) == 2


class TestBuildEntries:
    def test_sorted_by_code_for_a_stable_artifact(self):
        """這個檔案會進 git，順序不穩定會讓每次重建都產生無意義的差異。"""
        formulas = [
            ("002", "<div>名稱</div><div>乙方</div><div>處方</div><div>甘草3。</div>"),
            ("001", "<div>名稱</div><div>甲方</div><div>處方</div><div>麻黃4。</div>"),
        ]

        entries = build_entries(formulas, [])

        assert [e["code"] for e in entries] == ["P001", "P002"]

    def test_formulas_and_items_are_merged(self):
        formulas = [("001", "<div>名稱</div><div>甲方</div><div>處方</div><div>麻黃4。</div>")]
        items = [("herb", "<tr><td>001</td><td>丁香</td><td>X</td><td>Clove</td></tr>")]

        entries = build_entries(formulas, items)

        assert {e["kind"] for e in entries} == {"formula", "herb"}


class TestResolveOutputPath:
    def test_rejects_paths_outside_the_project(self):
        with pytest.raises(ValueError):
            resolve_output_path("/etc/passwd", argument="--output")

    def test_accepts_a_relative_path_inside_the_project(self):
        assert resolve_output_path(
            "resources/tcm_catalog.json", argument="--output"
        ).endswith("resources/tcm_catalog.json")
