"""成分重複偵測的判定門檻。

這支測試釘住的是「什麼情況值得驚動全家」——門檻放寬會讓警報變成雜訊，
收緊會漏掉真正的過量風險。
"""

import json

import pytest

from app.services.safety.ingredient_overlap import (
    IngredientWatchlist,
    find_overlap,
    should_check,
)


def _watchlist(*names: str) -> IngredientWatchlist:
    return IngredientWatchlist(names)


class TestFindOverlap:
    def test_reports_watched_ingredient_shared_by_both(self):
        result = find_overlap(
            ["ACETAMINOPHEN", "CAFFEINE ANHYDROUS"],
            ["ACETAMINOPHEN", "RIBOFLAVIN"],
            _watchlist("ACETAMINOPHEN"),
        )

        assert result.ingredients == ("ACETAMINOPHEN",)
        assert bool(result) is True

    def test_ignores_shared_ingredient_outside_the_watchlist(self):
        """維生素重複是常態且無臨床意義——報出來會淹掉真正該看的那一則。

        實測非處方藥最常見的 20 種成分裡有一半是維生素，全成分比對會讓警報
        變成背景雜訊。
        """
        result = find_overlap(
            ["RIBOFLAVIN", "ASCORBIC ACID"],
            ["RIBOFLAVIN", "ASCORBIC ACID"],
            _watchlist("ACETAMINOPHEN"),
        )

        assert result.ingredients == ()
        assert bool(result) is False

    def test_no_shared_ingredient(self):
        result = find_overlap(
            ["ACETAMINOPHEN"], ["IBUPROFEN"], _watchlist("ACETAMINOPHEN", "IBUPROFEN")
        )
        assert result.ingredients == ()

    def test_multiple_watched_overlaps_are_sorted(self):
        """順序固定，讓同一組輸入永遠產生同一則訊息。

        訊息內容會進推播，順序不穩定會讓相同狀況看起來像不同事件。
        """
        result = find_overlap(
            ["CHLORPHENIRAMINE MALEATE", "ACETAMINOPHEN"],
            ["ACETAMINOPHEN", "CHLORPHENIRAMINE MALEATE"],
            _watchlist("ACETAMINOPHEN", "CHLORPHENIRAMINE MALEATE"),
        )

        assert result.ingredients == ("ACETAMINOPHEN", "CHLORPHENIRAMINE MALEATE")

    def test_case_and_whitespace_are_defensive_only(self):
        """呼叫端可能餵未正規化的資料（使用者輸入、舊版藥證庫）。"""
        result = find_overlap(
            ["  acetaminophen "], ["ACETAMINOPHEN"], _watchlist("acetaminophen")
        )
        assert result.ingredients == ("ACETAMINOPHEN",)

    def test_empty_inputs_report_nothing(self):
        w = _watchlist("ACETAMINOPHEN")
        assert find_overlap([], ["ACETAMINOPHEN"], w).ingredients == ()
        assert find_overlap(["ACETAMINOPHEN"], [], w).ingredients == ()
        assert find_overlap(["", "  "], ["", "  "], w).ingredients == ()

    def test_empty_watchlist_reports_nothing(self):
        """白名單載入失敗時效果是「不偵測」，不是「全部都報」。"""
        result = find_overlap(
            ["ACETAMINOPHEN"], ["ACETAMINOPHEN"], IngredientWatchlist([])
        )
        assert result.ingredients == ()


class TestShouldCheck:
    def test_otc_classes_are_checked(self):
        assert should_check("otc") is True
        assert should_check("otc_guided") is True

    def test_prescription_is_not_checked(self):
        """處方藥經過醫師診斷與藥師調劑，再警示一次只是噪音。"""
        assert should_check("prescription") is False

    def test_non_medicine_is_not_checked(self):
        assert should_check("not_a_medicine") is False

    def test_unknown_class_is_not_checked(self):
        """未知分級一律不檢查——寧可少偵測，不要對不知道是什麼的東西發警報。

        `classify_drug` 對認不得的類別回空字串而不猜，這裡承接同一個方向。
        """
        assert should_check("") is False
        assert should_check(None) is False
        assert should_check("某個新分級") is False


class TestWatchlistLoading:
    def test_loads_names_from_file(self, tmp_path):
        path = tmp_path / "watch.json"
        path.write_text(
            json.dumps({"ingredients": [{"name": "ACETAMINOPHEN"}, {"name": "X"}]}),
            encoding="utf-8",
        )

        watchlist = IngredientWatchlist.load_from_path(str(path))

        assert len(watchlist) == 2
        assert "ACETAMINOPHEN" in watchlist

    def test_missing_file_yields_empty_watchlist_without_raising(self):
        """讀不到不能拋錯——那會讓整個掃描流程掛掉。"""
        watchlist = IngredientWatchlist.load_from_path("/nonexistent/watch.json")

        assert watchlist.is_empty
        assert len(watchlist) == 0

    def test_malformed_file_yields_empty_watchlist(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json at all", encoding="utf-8")

        assert IngredientWatchlist.load_from_path(str(path)).is_empty

    def test_real_watchlist_file_is_loadable_and_excludes_vitamins(self):
        """實際的白名單檔要能載入，且不得含維生素。

        維生素被納入會讓警報淪為雜訊，這條斷言擋住「順手多加幾個」。
        """
        watchlist = IngredientWatchlist.load_from_path()

        assert not watchlist.is_empty
        assert "ACETAMINOPHEN" in watchlist
        for vitamin in ("RIBOFLAVIN", "ASCORBIC ACID", "NIACINAMIDE",
                        "PYRIDOXINE HCL", "CYANOCOBALAMIN"):
            assert vitamin not in watchlist, f"維生素不該進白名單：{vitamin}"


# ── 局部用藥劑型 ────────────────────────────────────────────────────
# 實測隨機配對時出現「眼藥水 + 止咳糖漿」同時含 CHLORPHENIRAMINE MALEATE
# 而被判為重複。眼藥水是局部使用，這種重複沒有臨床意義。


class TestLocalActionForms:
    def test_unambiguous_local_forms_are_excluded(self):
        from app.services.safety.ingredient_overlap import (
            is_local_action,
            load_local_action_forms,
        )

        forms = load_local_action_forms()
        for form in ("點眼液劑", "點耳液劑", "陰道錠", "含漱劑"):
            assert is_local_action(form, forms), form

    def test_systemically_absorbed_forms_are_not_excluded(self):
        """外用不等於不吸收。

        穿皮貼片與栓劑都是刻意設計成全身吸收的；軟膏的水楊酸類也有經皮吸收
        累加風險（見白名單中 METHYL SALICYLATE 的納入理由）。把它們排除掉會
        漏掉真正的過量風險。
        """
        from app.services.safety.ingredient_overlap import (
            is_local_action,
            load_local_action_forms,
        )

        forms = load_local_action_forms()
        for form in ("穿皮貼片劑", "栓劑", "軟膏劑", "藥膠布", "錠劑", "糖漿劑"):
            assert not is_local_action(form, forms), form

    def test_unknown_form_is_compared_normally(self):
        """認不得的劑型照常比對。

        非處方藥有 100 種以上劑型，清單刻意不窮舉。寧可多報一則局部用藥的
        重複，也不要因為一條猜出來的規則漏掉真正的過量風險。
        """
        from app.services.safety.ingredient_overlap import (
            is_local_action,
            load_local_action_forms,
        )

        forms = load_local_action_forms()
        assert not is_local_action("某種新劑型", forms)
        assert not is_local_action("", forms)
        assert not is_local_action(None, forms)

    def test_missing_file_yields_empty_form_set(self):
        """讀不到時回空集合，效果是「全部照常比對」——不因設定檔缺席而漏報。"""
        from app.services.safety.ingredient_overlap import load_local_action_forms

        assert load_local_action_forms("/nonexistent/watch.json") == frozenset()
