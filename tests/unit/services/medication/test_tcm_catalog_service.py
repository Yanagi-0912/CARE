"""中藥庫比對的辨識規則。

這支測試釘住的是「什麼字串會被認成中藥」。放寬會把西藥誤判成中藥（然後拿
中藥的組成去查交互作用，結論完全是錯的）；收緊只是少偵測。
"""

from app.services.medication.tcm_catalog_service import (
    TcmCatalogEntry,
    TcmCatalogService,
)

GE_GEN = TcmCatalogEntry(code="P025", name_zh="葛根湯", herbs=("葛根", "麻黃", "桂枝"))
SHENG_MA = TcmCatalogEntry(code="P100", name_zh="升麻葛根湯", herbs=("升麻", "葛根", "白芍"))
SI_WU = TcmCatalogEntry(code="P007", name_zh="四物湯", herbs=("熟地黃", "白芍", "當歸", "川芎"))
TAO_HONG = TcmCatalogEntry(code="P008", name_zh="桃紅四物湯", herbs=("桃仁", "紅花", "當歸"))


def _service(*entries: TcmCatalogEntry) -> TcmCatalogService:
    return TcmCatalogService(entries or (GE_GEN, SHENG_MA, SI_WU, TAO_HONG))


class TestMatch:
    def test_exact_formula_name(self):
        assert _service().match("葛根湯").name_zh == "葛根湯"

    def test_brand_name_on_the_bag_still_resolves(self):
        """藥袋印的常是商品名，方名只是其中一段。"""
        assert _service().match("順天堂葛根湯濃縮顆粒").name_zh == "葛根湯"

    def test_longest_match_wins(self):
        """200 個方名裡有 9 對互為子字串。取最短或取第一個會把加味方誤判成
        基準方，而兩者的組成不同——桃紅四物湯有桃仁紅花，四物湯沒有。"""
        service = _service()

        assert service.match("桃紅四物湯").name_zh == "桃紅四物湯"
        assert service.match("升麻葛根湯").name_zh == "升麻葛根湯"

    def test_western_drug_is_not_mistaken_for_tcm(self):
        assert _service().match("普拿疼錠500毫克") is None
        assert _service().match("LIPITOR TABLETS 10MG") is None

    def test_blank_and_empty_catalog_are_safe(self):
        assert _service().match("") is None
        assert TcmCatalogService([]).match("葛根湯") is None
        assert TcmCatalogService([]).is_empty


class TestInteractionKeys:
    def test_formula_name_comes_first(self):
        """使用者吃的是「葛根湯」，訊息講方名他看得懂，講「葛根」他不知道
        那是哪一包。順序由這裡決定。"""
        assert GE_GEN.interaction_keys == ("葛根湯", "葛根", "麻黃", "桂枝")


class TestShippedCatalog:
    def setup_method(self):
        self.service = TcmCatalogService.load_from_path()

    def test_loads_the_two_hundred_standard_formulas(self):
        assert len(self.service) == 200

    def test_concentrated_preparations_are_not_used_for_identification(self):
        """藥典的 9 筆「濃縮製劑」與 200 基準方重複，但它們的 herbs 是自己的
        名字而不是組成藥材——拿它們辨識會得到一個查不到任何交互作用的鍵。"""
        entry = self.service.match("小青龍湯濃縮製劑")

        assert entry is not None
        assert entry.name_zh == "小青龍湯"
        assert "麻黃" in entry.herbs

    def test_single_herb_names_do_not_identify(self):
        """477 種藥材名裡有 224 種只有 1～2 字，拿去比對西藥品名撞出 565 筆
        （「人參」撞到人參萃取粉劑）。藥材名 SHALL NOT 用於辨識。"""
        assert self.service.match("人參") is None
        assert self.service.match("人參萃取粉劑３０％") is None
        assert self.service.match("甘草") is None
