import json
import random
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from app.services.medication.drug_catalog_service import (
    _MIN_CONTAINMENT_LENGTH,
    DrugCatalogEntry,
    DrugCatalogMatch,
    DrugCatalogService,
    normalize_drug_name,
)

REAL_CATALOG_PATH = Path(__file__).resolve().parents[4] / "resources" / "drug_catalog.json"

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


def test_load_from_path_populates_appearance_fields(tmp_path):
    path = tmp_path / "drug_catalog.json"
    path.write_text(
        json.dumps(
            [
                {
                    "license_number": "衛署藥製字第000002號",
                    "name_zh": "立普妥錠10毫克",
                    "name_en": "LIPITOR TABLETS 10MG",
                    "image_url": "https://example.test/a.jpg",
                    "shape": "圓形",
                    "color": "白色",
                    "score_line": "無",
                    "mark_one": "LIPITOR",
                    "mark_two": "10",
                    "size": "8mm",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = DrugCatalogService.load_from_path(str(path), threshold=0.88)
    match = service.match("立普妥錠10毫克")

    assert match is not None
    (entry,) = match.candidates
    assert entry.image_url == "https://example.test/a.jpg"
    assert entry.shape == "圓形"
    assert entry.color == "白色"
    assert entry.score_line == "無"
    assert entry.mark_one == "LIPITOR"
    assert entry.mark_two == "10"
    assert entry.size == "8mm"


def test_load_from_path_tolerates_entries_from_an_older_build_without_appearance_fields(
    tmp_path,
):
    """drug_catalog.json 是提交進 repo 的產出物，舊 commit 的檔案沒有外觀
    欄位。載入不能因為缺欄位而壞掉——缺的欄位一律當成空字串，而不是讓
    整個服務降級成空。"""
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
    match = service.match("立普妥錠10毫克")

    assert service.is_empty is False
    assert match is not None
    (entry,) = match.candidates
    assert entry.image_url == ""
    assert entry.shape == ""
    assert entry.color == ""
    assert entry.score_line == ""
    assert entry.mark_one == ""
    assert entry.mark_two == ""
    assert entry.size == ""


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


# ── 反方向含容比對必須是精確聯集，不是近似（Task 1 code review 發現）──
#
# `_candidates` 只聯集查詢裡「最罕見的一個 gram」的 postings 來補反方向
# 的召回率（候選鍵是查詢的子字串）。這個近似天生不完整：候選鍵越短，
# 自己的 gram 數量越少，落在查詢裡那個被挑中的稀有 gram 的機率也越低，
# 會被系統性地漏掉。真實案例：食藥署藥品許可證庫裡有藥證單獨以劑型
# 名稱掛證（「注射液」兩張、「膠囊」「膜衣錠」「軟膏」「糖漿」各一張），
# 查詢一個完整品名「潔毒注射液」時，「注射液」理應因為是查詢的子字串
# 而進入候選聯集，近似索引卻找不到它——結果 license_number 被判定為
# 唯一確定，但真正的聯集有 3 張證號。下游會據此貼給使用者一張看似
# 篤定、實則錯誤的藥丸照片，是本次 change 存在的理由要擋的正是這種
# 錯誤。

BARE_INJECTION_FORM_A = DrugCatalogEntry(license_number="衛署藥輸字第021715號", name_zh="注射液")
BARE_INJECTION_FORM_B = DrugCatalogEntry(license_number="衛署藥輸字第018726號", name_zh="注射液")
JIEDU_INJECTION = DrugCatalogEntry(license_number="衛署藥輸字第008820號", name_zh="潔毒注射液")


def test_bare_dosage_form_registered_alone_is_reachable_as_containment_candidate():
    """迴歸測試：真實案例「潔毒注射液」。修法前這裡的 license_number 會被
    誤判為 '衛署藥輸字第008820號'（看似唯一），但「注射液」本身另外
    掛著兩張證號，真正的聯集是 3 張，必須留空並讓三張都進候選。"""
    service = DrugCatalogService(
        [JIEDU_INJECTION, BARE_INJECTION_FORM_A, BARE_INJECTION_FORM_B], threshold=0.88
    )

    match = service.match("潔毒注射液")

    assert match is not None
    assert match.license_number is None
    assert {c.license_number for c in match.candidates} == {
        JIEDU_INJECTION.license_number,
        BARE_INJECTION_FORM_A.license_number,
        BARE_INJECTION_FORM_B.license_number,
    }


# ── reverse-only 命中不得單獨建立驗證結果（Task 1 第二輪 code review 發現）──
#
# 上面那次修復把反方向含容比對變精確之後，反而讓一條先前形同死路的
# 路徑活了起來：「候選鍵是查詢字串的子字串」只代表查詢字串裡剛好包含
# 某個登記過的片段（常見的是「膜衣錠」這類劑型名稱），不代表查詢字串
# 本身是真實藥名——任何字串，包含模型讀錯或完全虛構的藥名，只要恰好
# 含有這種短詞就會湊出命中。若把這種命中當成驗證證據，會讓「這個字串
# 不是任何真實藥名」的偵測整個失效，直接違背本模組存在的唯一理由。
# reverse 命中只能在已經有 exact 或 forward 證據時用來拆掉唯一性
# （見上面「潔毒注射液」的測試），不能在完全沒有其他證據時單獨把
# match() 的結果從「未驗證」變成「已驗證」。

BARE_FILM_COATED_TABLET = DrugCatalogEntry(
    license_number="衛部藥輸字第026602號", name_zh='"康普萊"膜衣錠'
)


def test_reverse_only_hit_does_not_verify_an_absent_drug():
    """真實案例：藥證庫裡沒有「克雷伯膜衣錠5毫克」這個藥證，也沒有任何
    品名把它整個包在裡面（forward 命中為空）。它只是剛好包含了單獨
    掛證的「膜衣錠」——這不構成藥名已驗證的證據，必須回傳 None，
    交給人工核對，而不是誤判成已驗證甚至證號唯一確定。"""
    service = DrugCatalogService([BARE_FILM_COATED_TABLET], threshold=0.88)

    assert service.match("克雷伯膜衣錠5毫克") is None


def test_reverse_only_hit_does_not_verify_an_ocr_misread():
    """真實案例：視覺模型把「冠脂妥膜衣錠10毫克」讀錯一個字變成
    「冠脂托膜衣錠10毫克」。藥證庫裡沒有這個錯字字串，也不是任何真實
    品名的一部分，唯一的命中訊號只有「膜衣錠」這個 reverse 命中——
    這正是本模組要偵測的「模型錯讀」情境，絕不能因為剛好含有一個
    登記過的劑型名稱就被判定為已驗證。"""
    service = DrugCatalogService([BARE_FILM_COATED_TABLET], threshold=0.88)

    assert service.match("冠脂托膜衣錠10毫克") is None


def test_reverse_hit_still_widens_candidates_when_exact_present():
    """對照：有 exact 命中時，reverse 命中依然要聯集進來拆掉唯一性——
    這是前一次修復要保留的行為（見「潔毒注射液」的測試），這次的方向
    不對稱規則不能連這個都削弱掉。"""
    real_drug = DrugCatalogEntry(
        license_number="衛署藥輸字第024131號", name_zh="冠脂妥膜衣錠10毫克"
    )
    service = DrugCatalogService([real_drug, BARE_FILM_COATED_TABLET], threshold=0.88)

    match = service.match("冠脂妥膜衣錠10毫克")

    assert match is not None
    assert match.license_number is None
    assert {c.license_number for c in match.candidates} == {
        real_drug.license_number,
        BARE_FILM_COATED_TABLET.license_number,
    }


# ── 純標點符號的鍵不進索引（Task 1 第二輪 code review 發現）────────────
#
# 藥證庫原始資料裡混著整個品名就是「.」或「*」這類純標點符號的資料
# 瑕疵。這種鍵不具任何辨識力，卻會在反方向含容比對裡跟任何帶有這個
# 符號的字串（例如英文縮寫的句點 "F.C."）湊出虛假的子字串命中。

JUNK_PERIOD_ENTRY = DrugCatalogEntry(license_number="衛署成製字第012731號", name_zh=".")


def test_punctuation_only_key_is_excluded_from_index():
    """純標點符號的品名不該進 `_by_key`——用它去查詢應該找不到任何東西，
    這張條目本身還在 `self._entries`，只是沒有可用的索引鍵。"""
    service = DrugCatalogService([JUNK_PERIOD_ENTRY], threshold=0.88)

    assert service.match(".") is None
    assert service.is_empty is False


def test_english_name_with_period_is_not_derailed_by_junk_key():
    """真實案例：英文藥名常見縮寫帶句點（"F.C. TABLETS"）。純標點的
    雜訊鍵「.」不該把它拖進候選，讓原本能唯一確定的證號平白變成
    找不到或多候選。"""
    real_drug = DrugCatalogEntry(
        license_number="衛署藥輸字第099999號",
        name_zh="立普康",
        name_en="LIPICOR F.C. TABLETS 40MG",
    )
    service = DrugCatalogService([real_drug, JUNK_PERIOD_ENTRY], threshold=0.88)

    match = service.match("LIPICOR F.C. TABLETS 40MG")

    assert match is not None
    assert match.license_number == real_drug.license_number


# ── 索引與暴力掃描在真實規模下仍一致 ────────────────────────────────────
#
# 前面 `test_index_produces_same_results_as_brute_force_scan` 只餵 16 筆
# 固定資料集——這個規模下 `_MAX_RARE_GRAM_POSTINGS`（3000）永遠不會
# 生效，postings 也永遠不完整才會失真，等於在「近似捷徑不可能失效」
# 的條件下斷言「索引等於暴力掃描」，這個斷言在那個規模下不可能失敗，
# 也就掩蓋了上面那個真實漏洞（見 code review）。改用真實藥證庫：
# 66,478 筆藥證、112,230 個正規化鍵，常見 gram 的 postings 遠超過
# 上限，是這個等價性斷言第一次有機會真正失敗的規模。


def _brute_force_forward_and_reverse(by_key: dict, key: str) -> tuple[set, set]:
    """完全不透過索引，對全部鍵做一次線性掃描，分別算出 forward
    （查詢是候選鍵的子字串）與 reverse（候選鍵是查詢字串的子字串）
    兩個方向的真正命中鍵集合。

    兩個方向分開算、不直接聯集，是因為它們的證據力不對稱（見
    `match()` 的判斷式）：forward 代表查詢字串整個對應到一張真實登記
    品名的一部分，reverse 只代表查詢字串剛好包含某個登記片段。這裡
    故意不重用 `_candidates`／`_reverse_containment_hits`，因為那正是
    被測對象，拿它當 ground truth 沒有意義。

    低於 `_MIN_CONTAINMENT_LENGTH` 的查詢一律跳過含容比對，只看完全
    比對——這是既有規則（見該常數的說明：極短查詢幾乎是任何品名的
    子字串，含容比對會失去意義），不是這次修的漏洞，ground truth 必須
    照著同一條規則算，否則會拿「服務故意不做的事」來冤枉它漏東西。
    """
    forward: set = set()
    reverse: set = set()
    if len(key) >= _MIN_CONTAINMENT_LENGTH:
        for candidate_key in by_key:
            if key in candidate_key:
                forward.add(candidate_key)
            elif candidate_key in key:
                reverse.add(candidate_key)
    return forward, reverse


def _brute_force_match_result(by_key: dict, key: str) -> tuple[bool, dict]:
    """完全不透過索引算出的真正比對結果：(是否已驗證, 聯集後的
    license_number → entry)。

    只有 exact 或 forward 命中非空才算已驗證（見 `match()` 的判斷式與
    模組文件「證據力」那段）；未驗證時第二個回傳值恆為空字典，呼叫端
    不該去看它——reverse-only 的候選不具驗證意義，聯集內容沒有正確
    答案可言。
    """
    exact = by_key.get(key)
    forward, reverse = _brute_force_forward_and_reverse(by_key, key)
    verified = exact is not None or bool(forward)
    if not verified:
        return False, {}

    entries_by_licence: dict[str, DrugCatalogEntry] = {}
    if exact is not None:
        entries_by_licence.update(exact)
    for candidate_key in forward | reverse:
        entries_by_licence.update(by_key[candidate_key])
    return True, entries_by_licence


# 已知單獨掛證、會產生 reverse 命中的劑型片段，用來構造「reverse-only」
# 的查詢（見下面 `_absent_drug_queries` 的說明）。
_REVERSE_ONLY_PROBES = ("膜衣錠", "注射液", "膠囊", "軟膏", "糖漿")


def _absent_drug_queries(rng: random.Random, count: int) -> list[str]:
    """構造保證落在「reverse-only」情境的查詢：隨機亂數字元接上一個
    已知單獨掛證的劑型片段。

    亂數字元刻意選在 Unicode 私人使用區（U+E000–U+F8FF）——這個區段
    不會出現在任何真實藥名裡，所以整段查詢字串保證不等於、也保證不是
    任何真實登記品名的一部分（沒有 exact、沒有 forward），但仍然完整
    包含這個劑型片段（保證有 reverse 命中）。這精準對應 code review
    描述的「完全不存在的藥名剛好包含一個登記片段」這個情境，不必依賴
    隨機亂數「碰巧」造出這個情境。
    """
    queries = []
    for _ in range(count):
        gibberish = "".join(chr(rng.randint(0xE000, 0xF8FF)) for _ in range(rng.randint(3, 8)))
        probe = rng.choice(_REVERSE_ONLY_PROBES)
        queries.append(gibberish + probe)
    return queries


@pytest.mark.skipif(
    not REAL_CATALOG_PATH.is_file(), reason="resources/drug_catalog.json 未產出，略過真實規模測試"
)
def test_index_matches_brute_force_at_real_catalog_scale():
    with REAL_CATALOG_PATH.open(encoding="utf-8") as catalog_file:
        raw_entries = json.load(catalog_file)
    entries = [
        DrugCatalogEntry(
            license_number=item["license_number"],
            name_zh=item.get("name_zh", ""),
            name_en=item.get("name_en", ""),
        )
        for item in raw_entries
    ]
    service = DrugCatalogService(entries, threshold=0.88)
    by_key = service._by_key

    rng = random.Random(20260815)
    sampled_keys = rng.sample([k for k in by_key if len(k) >= 3], 500)
    # 明確納入已知會被近似索引漏掉的案例，不能只靠隨機抽樣碰運氣抽中——
    # 這正是 code review 指出的具體漏洞，而且漏掉的長度範圍比一開始
    # 想的更廣：中文的單獨劑型掛證（2～3 字）、中文的「劑型+劑量」掛證
    # （如 8 字的「膜衣錠100毫克」）、英文的單獨原料藥掛證（如 22 字的
    # 「TESTOSTERONEPROPIONATE」）都出現過，且都是被同一種近似捷徑
    # （只挑查詢裡「最罕見的一個 gram」去聯集）系統性漏掉，不是各自獨立
    # 的巧合，長度上限治不了根本問題，這也是最終改用前綴分桶精確比對
    # 而不是再調一次長度上限的原因。
    known_bare_forms = [
        normalize_drug_name(name)
        for name in (
            "潔毒注射液",
            "注射液",
            "膠囊",
            "膜衣錠",
            "軟膏",
            "糖漿",
            "威達挺膜衣錠100毫克",
            "TESTOSTERONEPROPIONATEINJECTIONN.Y.",
        )
    ]

    for key in sampled_keys + known_bare_forms:
        indexed = service.match(key)
        verified, true_union = _brute_force_match_result(by_key, key)

        assert verified, key  # 這批查詢全部取自真實存在的鍵，必然已驗證
        assert indexed is not None, key
        assert {c.license_number for c in indexed.candidates} == set(true_union), key
        if len(true_union) == 1:
            (only_licence,) = true_union
            assert indexed.license_number == only_licence, key
        else:
            assert indexed.license_number is None, (
                f"查詢 {key!r} 真候選數 {len(true_union)}，"
                f"但索引回傳唯一證號 {indexed.license_number!r}——假唯一"
            )

    # 反方向：完全不存在的藥名剛好包含一個登記過的劑型片段，必須維持
    # 未驗證——見 code review「a drug that is NOT in the catalogue now
    # resolves to a confidently wrong licence」。實測全庫規模下量測
    # 這類查詢的「假證號率」曾高達 13.1%，這裡直接對真實藥證庫斷言
    # 為 0%，把量測結果釘進自動化測試，而不是只留在一次性量測腳本裡。
    for key in _absent_drug_queries(rng, 200):
        verified, _ = _brute_force_match_result(by_key, key)
        assert not verified, key  # 構造方式保證：沒有 exact，也沒有 forward

        indexed = service.match(key)
        assert indexed is None, (
            f"查詢 {key!r} 沒有任何完全比對或 forward 命中，"
            f"只有 reverse 命中，match() 卻回傳非 None——reverse-only "
            f"不該單獨建立驗證結果"
        )
