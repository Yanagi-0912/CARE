"""ATC 類別配對的判定門檻。

這條規則補的是成分層級抓不到的東西：布洛芬配華法林成分不同、都不是抗膽鹼、
也沒有中藥，前三條規則全部落空，但那是最重要的成藥 × 處方藥組合之一，而且
健保雲端藥歷也看不到（成藥那側不在它的資料裡）。
"""

from app.services.safety.atc_interaction import (
    ClassPair,
    ClassPairTable,
    ClassSide,
    find_class_pair,
)

NSAID_X_ANTITHROMBOTIC = ClassPair(
    pair_id="nsaid_x_antithrombotic",
    a=ClassSide("消炎止痛藥", ("M01A", "N02BA")),
    b=ClassSide("抗凝血或抗血小板藥", ("B01A",)),
    risk_zh="出血風險增加",
)
NSAID_X_NSAID = ClassPair(
    pair_id="nsaid_x_nsaid",
    a=ClassSide("消炎止痛藥", ("M01A",)),
    b=ClassSide("消炎止痛藥", ("M01A",)),
    risk_zh="腸胃道出血",
)


class TestFindClassPair:
    def test_nsaid_against_anticoagulant(self):
        """布洛芬（M01AE01）配華法林（B01AA03）。"""
        result = find_class_pair(
            ["M01AE01"], ["B01AA03"], ClassPairTable([NSAID_X_ANTITHROMBOTIC])
        )

        assert result is not None
        assert result.pair_id == "nsaid_x_antithrombotic"
        assert result.new_label == "消炎止痛藥"
        assert result.existing_label == "抗凝血或抗血小板藥"

    def test_both_directions_are_checked(self):
        """新藥可能落在任一側：買了止痛藥、或買了含阿斯匹靈的成藥而正在吃
        NSAID。只查一個方向會漏掉一半。"""
        table = ClassPairTable([NSAID_X_ANTITHROMBOTIC])

        result = find_class_pair(["B01AC06"], ["M01AE01"], table)

        assert result is not None
        assert result.new_label == "抗凝血或抗血小板藥"
        assert result.existing_label == "消炎止痛藥"

    def test_same_class_pair_matches_two_different_members(self):
        """布洛芬配雙氯芬酸：成分不同，現有的成分重複規則抓不到，但藥理上
        就是同一類藥吃了兩份。"""
        result = find_class_pair(
            ["M01AE01"], ["M01AB05"], ClassPairTable([NSAID_X_NSAID])
        )

        assert result is not None
        assert result.pair_id == "nsaid_x_nsaid"

    def test_unrelated_classes_do_not_match(self):
        table = ClassPairTable([NSAID_X_ANTITHROMBOTIC, NSAID_X_NSAID])

        assert find_class_pair(["M01AE01"], ["A11GA01"], table) is None
        assert find_class_pair(["N05BA01"], ["B01AA03"], table) is None

    def test_prefix_matching_is_hierarchical(self):
        """ATC 是階層碼，前綴比對就是「屬不屬於這個類別」。"""
        table = ClassPairTable([NSAID_X_ANTITHROMBOTIC])

        assert find_class_pair(["M01AB05"], ["B01AF01"], table) is not None
        assert find_class_pair(["M01AC06"], ["B01AC04"], table) is not None

    def test_any_of_several_codes_may_match(self):
        """一張藥證可能有多個 ATC 碼（複方藥、或同一藥多種用途），任一命中
        即算——複方藥的次要成分正是交互作用要看的東西。"""
        result = find_class_pair(
            ["A02BA01", "M01AE01"], ["N02BE01", "B01AA03"],
            ClassPairTable([NSAID_X_ANTITHROMBOTIC]),
        )

        assert result is not None

    def test_missing_atc_codes_never_match(self):
        """atc_codes 覆蓋率是 58.4%，查無 ATC 是常態。退化方向是少偵測。"""
        table = ClassPairTable([NSAID_X_ANTITHROMBOTIC])

        assert find_class_pair([], ["B01AA03"], table) is None
        assert find_class_pair(["M01AE01"], [], table) is None

    def test_empty_table_reports_nothing(self):
        assert find_class_pair(["M01AE01"], ["B01AA03"], ClassPairTable(())) is None

    def test_table_order_is_the_priority_order(self):
        both = ClassPairTable([NSAID_X_NSAID, NSAID_X_ANTITHROMBOTIC])
        result = find_class_pair(["M01AE01"], ["M01AB05", "B01AA03"], both)
        assert result.pair_id == "nsaid_x_nsaid"


class TestShippedTable:
    def setup_method(self):
        self.table = ClassPairTable.load_from_path()

    def test_table_loads(self):
        assert not self.table.is_empty

    def test_ibuprofen_and_warfarin_is_caught(self):
        """這條規則存在的理由。實測抗血栓藥 490 張全是處方藥、0 張非處方，
        因此觸發側必然是成藥——完全落在既有的觸發邊界內。"""
        result = find_class_pair(["M01AE01"], ["B01AA03"], self.table)

        assert result is not None
        assert result.pair_id == "nsaid_x_antithrombotic"
