"""同類累加（抗膽鹼疊加）的判定門檻。

與 test_ingredient_overlap.py 同一個目的：釘住「什麼情況值得驚動全家」。
這支測試特別要守住兩件事——不得與成分重複規則重複回報，以及不得因為比對
語意放寬而把不通過血腦障壁的四級銨算進來。
"""

import json

from app.services.safety.ingredient_overlap import (
    IngredientClass,
    find_class_stacking,
)


def _klass(*names: str) -> IngredientClass:
    return IngredientClass(names)


class TestFindClassStacking:
    def test_reports_two_different_ingredients_of_the_same_class(self):
        """這條規則存在的理由：感冒藥 + 暈車藥。

        成分名稱不同，`find_overlap` 的交集是空的，但抗膽鹼作用會加倍。
        """
        result = find_class_stacking(
            ["CHLORPHENIRAMINE MALEATE", "ACETAMINOPHEN"],
            ["DIMENHYDRINATE"],
            _klass("CHLORPHENIRAMINE MALEATE", "DIMENHYDRINATE"),
        )

        assert result is not None
        assert result.new_ingredient == "CHLORPHENIRAMINE MALEATE"
        assert result.existing_ingredient == "DIMENHYDRINATE"
        assert bool(result) is True

    def test_identical_ingredient_is_not_reported(self):
        """同成分已由 find_overlap 涵蓋，這裡再報一次會變成同一件事兩則訊息，
        而使用者該做的事完全一樣。"""
        result = find_class_stacking(
            ["CHLORPHENIRAMINE MALEATE"],
            ["CHLORPHENIRAMINE MALEATE"],
            _klass("CHLORPHENIRAMINE MALEATE"),
        )
        assert result is None

    def test_requires_both_sides_to_hit(self):
        """單一藥品命中不構成任何事件——這是配對規則，不是黑名單。"""
        assert (
            find_class_stacking(
                ["CHLORPHENIRAMINE MALEATE"],
                ["ACETAMINOPHEN"],
                _klass("CHLORPHENIRAMINE MALEATE", "DIMENHYDRINATE"),
            )
            is None
        )
        assert (
            find_class_stacking(
                ["ACETAMINOPHEN"],
                ["DIMENHYDRINATE"],
                _klass("CHLORPHENIRAMINE MALEATE", "DIMENHYDRINATE"),
            )
            is None
        )

    def test_still_reports_when_an_identical_pair_coexists(self):
        """兩邊都有 CHLORPHENIRAMINE，但新藥另含 DIPHENHYDRAMINE：
        不同成分的那一組仍然成立，不因為存在同成分就整組放棄。"""
        result = find_class_stacking(
            ["CHLORPHENIRAMINE MALEATE", "DIPHENHYDRAMINE HCL"],
            ["CHLORPHENIRAMINE MALEATE"],
            _klass(
                "CHLORPHENIRAMINE MALEATE",
                "DIPHENHYDRAMINE HCL",
            ),
        )
        assert result is not None
        assert result.new_ingredient == "DIPHENHYDRAMINE HCL"
        assert result.existing_ingredient == "CHLORPHENIRAMINE MALEATE"

    def test_pair_is_deterministic(self):
        """同一組輸入永遠產生同一則訊息。訊息會進推播，順序不穩定會讓相同
        狀況看起來像不同事件。"""
        klass = _klass("A INGREDIENT", "B INGREDIENT", "C INGREDIENT")
        first = find_class_stacking(
            ["C INGREDIENT", "B INGREDIENT"], ["A INGREDIENT"], klass
        )
        second = find_class_stacking(
            ["B INGREDIENT", "C INGREDIENT"], ["A INGREDIENT"], klass
        )
        assert first == second
        assert first.new_ingredient == "B INGREDIENT"

    def test_normalizes_case_and_whitespace(self):
        result = find_class_stacking(
            ["  chlorpheniramine maleate  "],
            ["dimenhydrinate"],
            _klass("CHLORPHENIRAMINE MALEATE", "DIMENHYDRINATE"),
        )
        assert result is not None

    def test_empty_class_reports_nothing(self):
        """清單載入失敗時退化成「不偵測」，比照整條路徑對主流程 fail-open。"""
        assert (
            find_class_stacking(
                ["CHLORPHENIRAMINE MALEATE"], ["DIMENHYDRINATE"], _klass()
            )
            is None
        )

    def test_blank_ingredients_are_ignored(self):
        assert (
            find_class_stacking(
                ["", "   "], ["DIMENHYDRINATE"], _klass("DIMENHYDRINATE")
            )
            is None
        )


class TestShippedList:
    """釘住實際出貨的清單內容，不只是判定邏輯。"""

    def setup_method(self):
        self.klass = IngredientClass.load_from_path()
        self.payload = json.load(
            open("resources/anticholinergic_ingredients.json", encoding="utf-8")
        )

    def test_list_loads(self):
        assert not self.klass.is_empty

    def test_quaternary_ammonium_is_excluded(self):
        """四級銨不通過血腦障壁，中樞抗膽鹼作用極小——BUTYLSCOPOLAMINE
        正因如此才被選用。以認知衰退與跌倒為理由的疊加判定不得納入它們。"""
        for name in (
            "SCOPOLAMINE BUTYLBROMIDE",
            "SCOPOLAMINE-N-BUTYLBROMIDE",
            "SCOPOLAMINE BROMOBUTYLATE",
            "HOMATROPINE METHYLBROMIDE",
            "ATROPINE METHYL BROMIDE",
        ):
            assert name not in self.klass, name

    def test_tertiary_amine_counterparts_are_kept(self):
        """母體的三級胺會通過血腦障壁，不能跟著四級銨一起被排除。"""
        for name in ("SCOPOLAMINE HBR", "ATROPINE SULFATE", "HOMATROPINE HBR"):
            assert name in self.klass, name

    def test_inhaled_lama_is_excluded(self):
        """ACLIDINIUM／UMECLIDINIUM 是吸入型長效抗蕈毒鹼，全身抗膽鹼負擔低，
        Beers Table 7 與 PIM-Taiwan Table 1 均未列入。子字串比對 CLIDINIUM
        會誤中它們，這是清單改用完全比對的直接原因。"""
        for name in ("MICRONISED ACLIDINIUM BROMIDE", "UMECLIDINIUM BROMIDE"):
            assert name not in self.klass, name

    def test_second_generation_antihistamines_are_excluded(self):
        """納入會把最常見的過敏用藥全部變成警報來源。"""
        for name in ("CETIRIZINE", "LORATADINE", "FEXOFENADINE"):
            assert name not in self.klass, name

    def test_every_entry_carries_its_measured_counts(self):
        """清單要能被非工程角色單獨審視：每一項都得看得到它涵蓋多少品項。"""
        for entry in self.payload["ingredients"]:
            assert entry["name"]
            assert entry["group"]
            assert entry["otc_products"] + entry["rx_products"] > 0
