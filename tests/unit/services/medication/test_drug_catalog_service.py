import json
from difflib import SequenceMatcher

import pytest

from app.services.medication.drug_catalog_service import (
    DrugCatalogEntry,
    DrugCatalogMatch,
    DrugCatalogService,
    normalize_drug_name,
)

AMLODIPINE = DrugCatalogEntry(
    license_number="衛署藥製字第000001號",
    name_zh='"福元"脈優錠5毫克',
    name_en='AMLODIPINE TABLETS 5MG "F.Y."',
)
ATORVASTATIN = DrugCatalogEntry(
    license_number="衛署藥製字第000002號",
    name_zh="立普妥錠10毫克",
    name_en="LIPITOR TABLETS 10MG",
)


def _service(threshold: float = 0.88) -> DrugCatalogService:
    return DrugCatalogService([AMLODIPINE, ATORVASTATIN], threshold=threshold)


def test_exact_chinese_name_matches():
    match = _service().match("立普妥錠10毫克")

    assert match is not None
    assert match.license_number == "衛署藥製字第000002號"
    assert match.name_zh == "立普妥錠10毫克"


def test_english_name_matches():
    match = _service().match("LIPITOR TABLETS 10MG")

    assert match is not None
    assert match.license_number == "衛署藥製字第000002號"


def test_manufacturer_prefix_is_ignored():
    """藥袋通常只印藥名，藥證上的品名卻帶著引號包住的廠商前綴。"""
    match = _service().match("脈優錠5毫克")

    assert match is not None
    assert match.license_number == "衛署藥製字第000001號"


def test_fullwidth_and_whitespace_differences_are_ignored():
    match = _service().match("立普妥錠 １０毫克")

    assert match is not None
    assert match.license_number == "衛署藥製字第000002號"


def test_unrelated_name_does_not_match():
    assert _service().match("銀杏葉萃取物膠囊") is None


def test_similar_name_below_threshold_does_not_match():
    """視覺模型把藥名讀成形近字時，比對必須落空而不是勉強命中。"""
    assert _service(threshold=0.99).match("脈定錠5毫克") is None


def test_same_name_matches_when_threshold_is_lowered():
    """對照上一個測試：落空來自門檻，不是因為比對根本沒運作。"""
    match = _service(threshold=0.5).match("脈定錠5毫克")

    assert match is not None
    assert match.license_number == "衛署藥製字第000001號"


def test_empty_name_does_not_match():
    assert _service().match("") is None


def test_empty_catalog_matches_nothing():
    assert DrugCatalogService([], threshold=0.88).match("立普妥錠10毫克") is None


def test_load_from_path_reads_entries(tmp_path):
    path = tmp_path / "drug_catalog.json"
    path.write_text(
        json.dumps(
            [
                {
                    "license_number": "衛署藥製字第000002號",
                    "name_zh": "立普妥錠10毫克",
                    "name_en": "LIPITOR TABLETS 10MG",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = DrugCatalogService.load_from_path(str(path), threshold=0.88)

    assert service.match("立普妥錠10毫克") is not None


def test_load_from_path_tolerates_missing_file(tmp_path):
    """藥證庫缺席不得讓應用啟動失敗；改為所有藥名都比對不到（降為低信心）。"""
    service = DrugCatalogService.load_from_path(
        str(tmp_path / "does_not_exist.json"), threshold=0.88
    )

    assert service.is_empty is True
    assert service.match("立普妥錠10毫克") is None


def test_load_from_path_tolerates_malformed_file(tmp_path):
    path = tmp_path / "drug_catalog.json"
    path.write_text("{not json", encoding="utf-8")

    service = DrugCatalogService.load_from_path(str(path), threshold=0.88)

    assert service.is_empty is True


def test_load_from_path_warns_when_the_file_parses_but_has_no_entries(tmp_path, caplog):
    """欄位名稱對不上 FDA 資料集時，載入不會拋例外——只會得到一個條目數為 0
    的服務，之後每份草稿都悄悄降為低信心。這種情況必須大聲記錄，不能只在
    「不存在」或「格式錯誤」時才出聲。"""
    path = tmp_path / "drug_catalog.json"
    path.write_text("[]", encoding="utf-8")

    with caplog.at_level("WARNING"):
        service = DrugCatalogService.load_from_path(str(path), threshold=0.88)

    assert service.is_empty is True
    assert any("條目數為 0" in record.message for record in caplog.records)


def test_load_from_path_logs_entry_count_on_success(tmp_path, caplog):
    path = tmp_path / "drug_catalog.json"
    path.write_text(
        json.dumps(
            [
                {
                    "license_number": "衛署藥製字第000002號",
                    "name_zh": "立普妥錠10毫克",
                    "name_en": "LIPITOR TABLETS 10MG",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with caplog.at_level("INFO"):
        service = DrugCatalogService.load_from_path(str(path), threshold=0.88)

    assert service.is_empty is False
    assert any("共 1 筆條目" in record.message for record in caplog.records)


# ── 含容比對 ──────────────────────────────────────────────────────────
#
# 藥袋通常只印短的品牌名（「普拿疼」），藥證上卻是連劑型、劑量都在內的
# 全名（「普拿疼錠500毫克」）。SequenceMatcher 對這種長度差懸殊的字串
# 算出的比值太低，任何門檻都分不開「這是同一顆藥的縮寫」跟「兩個不相干
# 的字串」（見模組 docstring），所以獨立出含容比對這個階段。

PANADOL_PLAIN = DrugCatalogEntry(
    license_number="衛署藥製字第010001號",
    name_zh="普拿疼錠500毫克",
)
PANADOL_COLD = DrugCatalogEntry(
    license_number="衛署藥製字第010002號",
    name_zh="普拿疼伏冒熱飲",
)
PANADOL_EXTRA = DrugCatalogEntry(
    license_number="衛署藥製字第010003號",
    name_zh="普拿疼加強錠",
)


def test_containment_hit_with_unique_license_matches():
    """「普拿疼」只落在一張藥證的品名裡時，比對出那一張證號。"""
    service = DrugCatalogService([PANADOL_PLAIN], threshold=0.88)

    match = service.match("普拿疼")

    assert match is not None
    assert match.license_number == "衛署藥製字第010001號"


def test_containment_hit_spanning_multiple_licenses_leaves_license_number_none():
    """「普拿疼」同時是三張不同藥證品名的子字串：藥名本身已驗證為真實
    存在的核准藥品（match 非 None），但無法判斷是哪一個品項，因此
    license_number 必須留 None，不得任選一張證號冒充答案。"""
    service = DrugCatalogService(
        [PANADOL_PLAIN, PANADOL_COLD, PANADOL_EXTRA], threshold=0.88
    )

    match = service.match("普拿疼")

    assert match is not None
    assert match.license_number is None
    assert {c.license_number for c in match.candidates} == {
        PANADOL_PLAIN.license_number,
        PANADOL_COLD.license_number,
        PANADOL_EXTRA.license_number,
    }


def test_query_below_minimum_containment_length_does_not_match_by_containment():
    """低於最小長度的查詢字串跳過含容比對；即使它確實是某個藥證品名的
    子字串，也不能因為含容比對而命中——否則「錠」這種字會匹配半個藥證庫
    （見模組常數 `_MIN_CONTAINMENT_LENGTH` 的說明）。這裡選的字串短到
    落回模糊比對後，相似度也不足以在預設門檻下命中，所以整體應該落空。"""
    service = DrugCatalogService(
        [DrugCatalogEntry(license_number="衛署藥製字第010004號", name_zh="疼痛安錠")],
        threshold=0.88,
    )

    assert service.match("疼痛") is None


# ── 索引與暴力掃描的一致性 ────────────────────────────────────────────


def _brute_force_match(
    by_key: dict[str, dict[str, DrugCatalogEntry]], threshold: float, name: str
):
    """不透過 gram 索引、直接對全部鍵做線性掃描的參考實作。

    完全比對∪含容比對、模糊比對的邏輯與 `DrugCatalogService` 完全相同，
    差別只在候選集合是「全部的鍵」而不是索引narrow 出來的子集——索引
    只是效能優化，不應該改變任何一筆查詢的最終結果，這支函式就是用來
    驗證這件事的 oracle。`by_key` 的值是 `{license_number: entry}`，
    跟服務內部的索引結構一致，才能餵服務本身建出來的 `_by_key` 進來比對。
    """

    def resolve(entries_by_license: dict[str, DrugCatalogEntry], score: float):
        candidates = list(entries_by_license.values())
        if len(candidates) == 1:
            (entry,) = candidates
            return DrugCatalogMatch(
                entry.license_number, entry.name_zh, entry.name_en, score, candidates
            )
        return DrugCatalogMatch(None, "", "", score, candidates)

    key = normalize_drug_name(name)
    if not key or not by_key:
        return None

    exact = by_key.get(key)
    hits = [k for k in by_key if key in k or k in key] if len(key) >= 3 else []

    if exact is not None or hits:
        entries_by_license: dict[str, DrugCatalogEntry] = {}
        for k in hits:
            entries_by_license.update(by_key[k])
        if exact is not None:
            entries_by_license.update(exact)
            return resolve(entries_by_license, 1.0)
        if len(entries_by_license) > 1:
            return resolve(entries_by_license, 0.0)
        (entry,) = entries_by_license.values()
        coverage = max(
            min(len(key), len(k)) / max(len(key), len(k))
            for k in hits
            if entry.license_number in by_key[k]
        )
        return resolve(entries_by_license, coverage)

    best_key = None
    best_score = 0.0
    for k in by_key:
        score = SequenceMatcher(None, key, k).ratio()
        if score > best_score:
            best_key, best_score = k, score
    if best_key is None or best_score < threshold:
        return None
    return resolve(by_key[best_key], best_score)


def test_index_produces_same_results_as_brute_force_scan():
    """對一份稍具規模、涵蓋完全比對／含容（單一與多重藥證）／模糊比對／
    未命中各種情形的固定資料集，逐一比對索引版與暴力掃描版的結果——
    確認索引只是候選集合的narrow ing，不會漏掉暴力掃描找得到的命中，
    也不會多出暴力掃描找不到的命中。"""
    entries = [
        AMLODIPINE,
        ATORVASTATIN,
        PANADOL_PLAIN,
        PANADOL_COLD,
        PANADOL_EXTRA,
        DrugCatalogEntry(
            license_number="衛署藥輸字第020001號",
            name_zh="冠脂妥膜衣錠10毫克",
            name_en="LIPITOR FILM-COATED TABLETS 10MG",
        ),
        DrugCatalogEntry(
            license_number="衛署藥輸字第020002號",
            name_zh="冠脂妥膜衣錠20毫克",
            name_en="LIPITOR FILM-COATED TABLETS 20MG",
        ),
        DrugCatalogEntry(license_number="衛署藥製字第030001號", name_zh="脈定錠5毫克"),
        DrugCatalogEntry(license_number="衛署藥製字第030002號", name_zh="銀杏葉萃取物膠囊"),
        DEDROGYL_EXACT,
        DEDROGYL_ABBOTT,
        DEDROGYL_OTHER_A,
        DEDROGYL_OTHER_B,
    ]
    threshold = 0.88
    service = DrugCatalogService(entries, threshold=threshold)

    queries = [
        "立普妥錠10毫克",  # 完全比對
        "脈優錠5毫克",  # 完全比對（去廠商前綴）
        "普拿疼",  # 含容比對，命中多張藥證
        "冠脂妥膜衣錠",  # 含容比對，命中多張藥證（不同劑量）
        "LIPITOR",  # 含容比對（英文），命中多張藥證
        "脈定錠5毫克",  # 未達門檻的模糊比對
        "銀杏葉萃取物膠囊",  # 完全比對
        "得胎隆",  # 完全比對唯一，但聯集含容比對後變成多張
        "這絕對不是一個藥名",  # 未命中
        "XYZQWERTY",  # 未命中
    ]

    for query in queries:
        indexed = service.match(query)
        reference = _brute_force_match(service._by_key, threshold, query)

        if reference is None:
            assert indexed is None, query
        else:
            assert indexed is not None, query
            assert indexed.license_number == reference.license_number, query
            assert indexed.score == pytest.approx(reference.score), query
            assert {c.license_number for c in indexed.candidates} == {
                c.license_number for c in reference.candidates
            }, query


# ── 證號唯一才可信（決策 1）───────────────────────────────────────────
#
# 全庫實測：112,230 個正規化鍵裡有 10,766 個對應到不只一張藥證，涉及
# 31,387 張（全庫 47%）。以下用「葉酸」模擬這種碰撞：兩張不同藥證的
# 品名正規化後完全相同。

FOLIC_ACID_A = DrugCatalogEntry(license_number="衛署藥製字第040001號", name_zh="葉酸")
FOLIC_ACID_B = DrugCatalogEntry(license_number="衛署藥輸字第040002號", name_zh="葉酸")


def test_unique_key_returns_license_number():
    """鍵唯一對應一張藥證時，license_number 正常有值——這是既有行為，
    這裡明確釘住它在候選模型下不受影響。"""
    service = DrugCatalogService([FOLIC_ACID_A], threshold=0.88)

    match = service.match("葉酸")

    assert match is not None
    assert match.license_number == "衛署藥製字第040001號"
    assert [c.license_number for c in match.candidates] == ["衛署藥製字第040001號"]


def test_colliding_key_leaves_license_number_none_with_both_candidates():
    """兩張藥證的品名正規化後相同：license_number 必須留空，
    兩張都要出現在候選清單——不得任選一張冒充答案。"""
    service = DrugCatalogService([FOLIC_ACID_A, FOLIC_ACID_B], threshold=0.88)

    match = service.match("葉酸")

    assert match is not None
    assert match.license_number is None
    assert {c.license_number for c in match.candidates} == {
        "衛署藥製字第040001號",
        "衛署藥輸字第040002號",
    }


def test_exact_match_on_colliding_key_still_leaves_license_number_none():
    """先前錯配的主因：完全比對（score 1.0）命中碰撞鍵時，
    license_number 仍必須為空。實測全庫 400 筆模擬中 4.8% 比到錯的
    藥證，其中 74% 就是這種「完全比對卻碰撞」的情形（「得胎隆」本身
    就是另一張藥證的完整品名）——score 是 1.0 不代表證號唯一確定。"""
    service = DrugCatalogService([FOLIC_ACID_A, FOLIC_ACID_B], threshold=0.88)

    match = service.match("葉酸")

    assert match is not None
    assert match.score == 1.0
    assert match.license_number is None


def test_second_colliding_license_is_reachable_not_silently_dropped():
    """先前用 setdefault 建索引時，碰撞鍵的第二筆永遠進不去索引，
    無論怎麼查都比對不到。這裡直接檢查兩張藥證各自都在候選清單裡出現
    過，確認第二張不再被靜默吞掉。"""
    service = DrugCatalogService([FOLIC_ACID_A, FOLIC_ACID_B], threshold=0.88)

    match = service.match("葉酸")

    assert match is not None
    licenses_in_candidates = [c.license_number for c in match.candidates]
    assert FOLIC_ACID_A.license_number in licenses_in_candidates
    assert FOLIC_ACID_B.license_number in licenses_in_candidates


def test_multi_candidate_match_is_still_a_non_none_result():
    """藥名信心度只看 `match()` 是不是 None（見
    `PrescriptionScanService._verify_against_catalog` 的說明），跟
    `license_number` 有沒有值是兩件事。這裡釘住候選多筆時 `match()`
    仍回傳非 None 的物件，這是信心度判定成立的前提。"""
    service = DrugCatalogService([FOLIC_ACID_A, FOLIC_ACID_B], threshold=0.88)

    match = service.match("葉酸")

    assert match is not None
    assert len(match.candidates) == 2


# ── 完全比對 ∪ 含容比對：唯一的鍵不代表消除了歧義 ─────────────────────
#
# 「得胎隆」完全比對只命中一張藥證，但同時也是另外幾張藥證品名的一部分
# （真實案例：「亞培」得胎隆膜衣錠10毫克）。只看完全比對命中的鍵是不是
# 唯一，會漏掉這種情形——候選集合必須是完全比對跟含容比對的聯集。

DEDROGYL_EXACT = DrugCatalogEntry(license_number="內衛藥輸字第002077號", name_zh="得胎隆")
DEDROGYL_ABBOTT = DrugCatalogEntry(
    license_number="衛署藥輸字第050001號", name_zh='"亞培"得胎隆膜衣錠10毫克'
)
DEDROGYL_OTHER_A = DrugCatalogEntry(license_number="衛署藥輸字第050002號", name_zh="得胎隆錠5毫克")
DEDROGYL_OTHER_B = DrugCatalogEntry(license_number="衛署藥輸字第050003號", name_zh="得胎隆糖衣錠")


def test_exact_match_unions_with_entries_whose_name_contains_the_query():
    """查詢字串的完全比對鍵唯一（只有 DEDROGYL_EXACT 這一張），但它同時
    是另外 3 張藥證品名的子字串。候選集合必須是兩者的聯集，讓
    license_number 留空、四張藥證都在候選——不能因為完全比對本身唯一
    就當作證號已確定，這正是先前「得胎隆比到別張藥證」錯配的根源。"""
    service = DrugCatalogService(
        [DEDROGYL_EXACT, DEDROGYL_ABBOTT, DEDROGYL_OTHER_A, DEDROGYL_OTHER_B],
        threshold=0.88,
    )

    match = service.match("得胎隆")

    assert match is not None
    assert match.score == 1.0
    assert match.license_number is None
    assert {c.license_number for c in match.candidates} == {
        DEDROGYL_EXACT.license_number,
        DEDROGYL_ABBOTT.license_number,
        DEDROGYL_OTHER_A.license_number,
        DEDROGYL_OTHER_B.license_number,
    }


def test_exact_match_stays_unique_when_nothing_else_contains_it():
    """對照上一個測試：完全比對命中的鍵如果沒有任何其他藥證的品名包含
    它，聯集後仍然只有一張，license_number 正常有值——聯集規則不會把
    原本就唯一的答案也變成留空。"""
    service = DrugCatalogService([DEDROGYL_EXACT], threshold=0.88)

    match = service.match("得胎隆")

    assert match is not None
    assert match.license_number == DEDROGYL_EXACT.license_number
