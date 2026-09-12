"""中西藥交互作用的判定門檻。

與 test_ingredient_overlap.py 同一個目的：釘住「什麼情況值得通知」。這裡另外
要守住兩件在真實資料上踩過的坑——CJK 相容字，以及西藥側的詞界前綴。
"""

from app.services.safety.tcm_interaction import (
    TcmInteractionTable,
    find_tcm_interaction,
    normalize_tcm_name,
    western_ingredient_matches,
)


def _table(*pairs) -> TcmInteractionTable:
    return TcmInteractionTable(
        [
            {"tcm": t, "western": w, "western_kind": k, "summary": s}
            for t, w, k, s in pairs
        ]
    )


class TestNormalizeTcmName:
    def test_nfkc_folds_cjk_compatibility_ideographs(self):
        """實測衛福部交互作用資料庫的「小青龍湯」用的是相容表意字 U+F9C4（龍），
        中藥庫用的是常用的 U+9F8D。**肉眼完全一樣，字串不相等**——沒有這一步，
        含麻黃的關鍵方劑之一會靜默地永遠比不到。"""
        compatibility = "小青" + "龍" + "湯"
        assert compatibility != "小青龍湯"
        assert normalize_tcm_name(compatibility) == "小青龍湯"

    def test_strips_dosage_form_and_parenthetical_markers(self):
        assert normalize_tcm_name("六味地黃丸《丸》") == "六味地黃丸"
        assert normalize_tcm_name("小青龍湯濃縮製劑（顆粒、散）") == "小青龍湯濃縮製劑"
        assert normalize_tcm_name("【飲片】丹參") == "丹參"

    def test_blank_is_safe(self):
        assert normalize_tcm_name("") == ""
        assert normalize_tcm_name(None) == ""


class TestWesternIngredientMatches:
    def test_exact_and_salt_forms_match(self):
        assert western_ingredient_matches("WARFARIN", "WARFARIN")
        assert western_ingredient_matches("WARFARIN", "WARFARIN SODIUM")
        assert western_ingredient_matches("ZOLPIDEM", "ZOLPIDEM TARTRATE")

    def test_requires_a_word_boundary(self):
        """沒有那個空白，NIACIN 會命中 NIACINAMIDE——兩種不同的東西。

        這是本模組與 IngredientClass 的分界：那邊完全禁用非完全比對（因為
        危險來自字串中間的包含），這邊只放行詞界前綴。
        """
        assert not western_ingredient_matches("NIACIN", "NIACINAMIDE")
        assert not western_ingredient_matches("CLIDINIUM", "UMECLIDINIUM BROMIDE")

    def test_case_and_whitespace_are_normalized(self):
        assert western_ingredient_matches("  warfarin ", "WARFARIN SODIUM")

    def test_blank_never_matches(self):
        assert not western_ingredient_matches("", "WARFARIN")
        assert not western_ingredient_matches("WARFARIN", "")


class TestFindTcmInteraction:
    def test_matches_at_formula_level(self):
        table = _table(("葛根湯", "ASPIRIN", "ingredient", "機制未明"))

        result = find_tcm_interaction(["葛根湯", "葛根", "麻黃"], ["ASPIRIN"], table)

        assert result is not None
        assert result.tcm_name == "葛根湯"
        assert result.western_ingredient == "ASPIRIN"

    def test_matches_at_herb_level(self):
        """配對表兩種層級都有：「葛根湯 × Aspirin」是方，「甘草 × 利尿劑」是
        藥材。只給其中一種會漏掉另一半。"""
        table = _table(("甘草", "FUROSEMIDE", "ingredient", ""))

        result = find_tcm_interaction(["芍藥甘草湯", "白芍", "甘草"], ["FUROSEMIDE"], table)

        assert result is not None
        assert result.tcm_name == "甘草"

    def test_formula_name_wins_when_both_could_match(self):
        """使用者吃的是「葛根湯」，訊息講方名他看得懂，講「葛根」他不知道那是
        哪一包。順序由呼叫端決定，這裡不排序。"""
        table = _table(
            ("葛根", "ASPIRIN", "ingredient", ""),
            ("葛根湯", "ASPIRIN", "ingredient", ""),
        )

        result = find_tcm_interaction(["葛根湯", "葛根"], ["ASPIRIN"], table)

        assert result.tcm_name == "葛根湯"

    def test_class_rows_are_not_matched(self):
        """中文藥理類別名對不上任何成分字串。悄悄拿它去比對只會全部落空，
        還讓人以為已經涵蓋了——要用得先以 ATC 建立類別對應。"""
        table = _table(("甘草", "利尿劑", "class", ""))

        assert find_tcm_interaction(["甘草"], ["FUROSEMIDE"], table) is None

    def test_no_match_returns_none(self):
        table = _table(("丹參", "WARFARIN", "ingredient", ""))

        assert find_tcm_interaction(["葛根湯"], ["WARFARIN SODIUM"], table) is None
        assert find_tcm_interaction(["丹參"], ["ASCORBIC ACID"], table) is None

    def test_empty_table_reports_nothing(self):
        """載入失敗時退化成「不偵測」，比照整條路徑對主流程 fail-open。"""
        assert find_tcm_interaction(["丹參"], ["WARFARIN"], TcmInteractionTable([])) is None

    def test_blank_inputs_are_ignored(self):
        table = _table(("丹參", "WARFARIN", "ingredient", ""))

        assert find_tcm_interaction([], ["WARFARIN"], table) is None
        assert find_tcm_interaction(["丹參"], [], table) is None
        assert find_tcm_interaction(["", "  "], ["WARFARIN"], table) is None

    def test_result_is_deterministic(self):
        table = _table(
            ("丹參", "WARFARIN", "ingredient", ""),
            ("丹參", "ASPIRIN", "ingredient", ""),
        )

        first = find_tcm_interaction(["丹參"], ["WARFARIN SODIUM", "ASPIRIN"], table)
        second = find_tcm_interaction(["丹參"], ["ASPIRIN", "WARFARIN SODIUM"], table)

        assert first == second


class TestShippedTable:
    """釘住實際出貨的配對表，不只是判定邏輯。"""

    def setup_method(self):
        self.table = TcmInteractionTable.load_from_path()

    def test_table_loads(self):
        assert not self.table.is_empty

    def test_danshen_warfarin_is_present(self):
        """丹參配抗凝血劑是中西藥併用最經典的出血風險組合。"""
        result = find_tcm_interaction(["丹參"], ["WARFARIN SODIUM"], self.table)

        assert result is not None
        assert result.western_ingredient == "WARFARIN"

    def test_ephedra_formulas_are_reachable(self):
        """葛根湯是含麻黃的方裡最常見的一帖，也是這整條路徑的動機。"""
        result = find_tcm_interaction(["葛根湯"], ["ASPIRIN"], self.table)

        assert result is not None
        assert result.tcm_name == "葛根湯"

    def test_summaries_carry_no_raw_newlines(self):
        """摘要會被呈現面讀到，保留來源的 \\r\\r\\n 只會產生破碎的排版。"""
        for entries in self.table._by_tcm.values():
            for _, summary in entries:
                assert "\n" not in summary and "\r" not in summary
