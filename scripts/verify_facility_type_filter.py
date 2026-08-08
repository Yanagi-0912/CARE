"""
Task 6：對真實 MongoDB 資料驗證「依院所類型篩選」功能。

前五個任務都只用 fake repository 在單元測試層級驗證過，這支腳本改對真實資料庫跑，
確認整條路徑（service 層查詢邏輯 + 工具層文案分支）在真實資料上真的成立。

**全程唯讀**：只使用 find / aggregate（$geoNear），不寫入、不更新、不建立索引。

驗證項目（對應 tasks.md 第 6 節）：
    6.2 台北車站「附近有大醫院嗎」→ 結果須全為醫院類，且與未套過濾的現況並列對照
    6.3 疊加「大醫院的腸胃科」→ 結果須同時滿足科別與類型兩條件，且比單一條件更窄
    6.4 藥局 → 查得到時 type 須為藥局類；查無時工具層須回專屬文案而非通用文案

執行方式：
    .venv/bin/python scripts/verify_facility_type_filter.py
"""

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.db.mongodb import MongoDBManager  # noqa: E402
from app.i18n.messages import t  # noqa: E402
from app.schemas import MedicalFacility  # noqa: E402
from app.services.medical.facility_type_matcher import (  # noqa: E402
    FACILITY_TYPE_CATEGORIES,
)
from app.services.medical.medical_service import (  # noqa: E402
    NEARBY_SEARCH_STEPS,
    MedicalService,
)
from app.tools.medical_tools import (  # noqa: E402
    configure_medical_tools,
    find_nearby_hospitals,
)

HOSPITAL_TYPES = frozenset(FACILITY_TYPE_CATEGORIES["醫院"])
CLINIC_TYPES = frozenset(FACILITY_TYPE_CATEGORIES["診所"])
PHARMACY_TYPES = frozenset(FACILITY_TYPE_CATEGORIES["藥局"])

TAIPEI_STATION = (25.0478, 121.5170)
# 蘭嶼：全台離島之一，四周無藥局，用來驗證「附近真的沒有藥局」的查無路徑。
LANYU = (22.05, 121.54)


def _print_header(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def _print_facilities(facilities: list[MedicalFacility]) -> None:
    if not facilities:
        print("  （無結果）")
        return
    for f in facilities:
        km = (f.distance_meters or 0) / 1000
        depts = f.departments if f.departments else []
        print(f"  {km:5.2f} km  [{f.type}]  {f.name}  科別={depts}")


def _contains_department(departments: list[str] | None, canonical: str) -> bool:
    """
    容忍 departments 欄位「整串科別擠在單一陣列元素」的髒資料格式，
    做法比照 build_department_query()：對每個陣列元素做子字串比對，
    而不是要求 canonical 恰好是陣列裡的一個獨立元素。
    """
    if not departments:
        return False
    return any(canonical in d for d in departments)


async def verify_6_2(service: MedicalService) -> None:
    """6.2：台北車站「附近有大醫院嗎」須回傳醫院類而非診所。"""
    _print_header("6.2 台北車站「附近有大醫院嗎」")
    lat, lng = TAIPEI_STATION

    baseline = await service.find_nearby_hospitals(lat, lng)
    print("\n[對照組] 未套用 facility_type 過濾（現況）：")
    _print_facilities(baseline.facilities)
    baseline_types = {f.type for f in baseline.facilities}
    print(f"  → 現況結果 type 集合：{baseline_types}")

    filtered = await service.find_nearby_hospitals(lat, lng, facility_type="大醫院")
    print("\n[套用 facility_type=\"大醫院\" 後]：")
    _print_facilities(filtered.facilities)

    assert filtered.facility_type_match is not None, "應解析出 facility_type_match"
    assert filtered.facility_type_match.category == "醫院"
    assert filtered.facilities, "台北車站 50 公里內應能找到至少一家醫院類院所"
    for f in filtered.facilities:
        assert f.type in HOSPITAL_TYPES, f"{f.name} 的 type={f.type!r} 不屬於醫院類四值"
        assert f.type not in CLINIC_TYPES, f"{f.name} 的 type={f.type!r} 竟然是診所類"

    print(
        f"\n[驗證通過] 套用類型過濾後共 {len(filtered.facilities)} 筆，"
        f"每一筆 type 都落在 {sorted(HOSPITAL_TYPES)} 之內，且不含任何診所類。"
    )
    if baseline_types and baseline_types.issubset(CLINIC_TYPES):
        print(
            "[對照] 現況（未過濾）的 5 筆确实全為診所類，"
            "與 proposal 記錄的問題現象一致，套用類型過濾後問題已解決。"
        )
    else:
        print(
            "[附註] 現況（未過濾）的最近 5 筆不再是清一色診所"
            "（可能是資料庫內容已變動），僅供參考，不影響本項驗證的通過與否。"
        )


async def verify_6_3(service: MedicalService) -> None:
    """6.3：疊加「大醫院的腸胃科」須同時滿足科別與類型兩條件，且比單一條件更窄。"""
    _print_header("6.3 疊加驗證：「大醫院的腸胃科」")
    lat, lng = TAIPEI_STATION

    combo = await service.find_nearby_facilities_by_department(
        lat, lng, department="腸胃科", facility_type="大醫院"
    )
    print("\n[科別=腸胃科 + facility_type=大醫院] 結果：")
    _print_facilities(combo.facilities)

    assert combo.match is not None, "應能解析「腸胃科」科別"
    assert combo.match.canonical == "內科", f"腸胃科應映射到內科，實際={combo.match.canonical!r}"
    assert combo.facility_type_match is not None
    assert combo.facility_type_match.category == "醫院"
    assert combo.facilities, "台北車站 50 公里內應能找到至少一家「醫院類 + 內科」院所"
    for f in combo.facilities:
        assert f.type in HOSPITAL_TYPES, f"{f.name} 的 type={f.type!r} 不屬於醫院類"
        assert _contains_department(f.departments, "內科"), (
            f"{f.name} 的 departments={f.departments!r} 不含「內科」"
        )
    print(
        f"\n[驗證通過] {len(combo.facilities)} 筆結果同時滿足"
        "「type ∈ 醫院類四值」與「departments 含內科」兩條件。"
    )

    # 用大 target_count（1000，遠超過真實資料量）把 50 公里內符合各條件的院所
    # 全部撈出來，藉此比較「只有科別」「只有類型」「疊加」三者的真實筆數，
    # 證明疊加確實比兩者都窄——而不是被預設 target_count=5 的階梯式湊數蓋掉差異。
    large_n = 1000
    dept_only = await service.find_nearby_facilities_by_department(
        lat, lng, department="腸胃科", target_count=large_n
    )
    type_only = await service.find_nearby_hospitals(
        lat, lng, target_count=large_n, facility_type="大醫院"
    )
    combo_large = await service.find_nearby_facilities_by_department(
        lat, lng, department="腸胃科", facility_type="大醫院", target_count=large_n
    )

    n_dept, n_type, n_combo = (
        len(dept_only.facilities),
        len(type_only.facilities),
        len(combo_large.facilities),
    )
    print(f"\n[50 公里內完整筆數比較] 只有科別（內科）={n_dept} 筆")
    print(f"[50 公里內完整筆數比較] 只有類型（醫院類）={n_type} 筆")
    print(f"[50 公里內完整筆數比較] 疊加（內科 + 醫院類）={n_combo} 筆")

    assert n_combo < n_dept, "疊加筆數應少於只有科別的筆數"
    assert n_combo < n_type, "疊加筆數應少於只有類型的筆數"
    print("[驗證通過] 疊加確實比兩個單一條件都窄。")


async def verify_6_4(service: MedicalService) -> None:
    """6.4：藥局須走專屬文案而非通用查無訊息；查得到時 type 須為藥局類。"""
    _print_header("6.4 藥局驗證")
    configure_medical_tools(service)

    # --- 台北車站：資料庫內離台北車站最近的藥局約 18 公里，50 公里內應查得到 ---
    lat, lng = TAIPEI_STATION
    result = await service.find_nearby_hospitals(lat, lng, facility_type="藥局")
    print("\n[台北車站 + facility_type=藥局]（service 層）結果：")
    _print_facilities(result.facilities)

    assert result.facility_type_match is not None
    assert result.facility_type_match.category == "藥局"

    tool_payload_taipei = await find_nearby_hospitals.ainvoke(
        {"lat": lat, "lng": lng, "facility_type": "藥局"}
    )
    print(f"\n[工具層回傳]（前 200 字）：{tool_payload_taipei[:200]}")

    if result.facilities:
        for f in result.facilities:
            assert f.type in PHARMACY_TYPES, f"{f.name} 的 type={f.type!r} 不屬於藥局類"
        print(
            f"[驗證通過] 台北車站查得到 {len(result.facilities)} 筆藥局，"
            "每一筆 type 皆為「藥師自營」或「藥劑生自營」。"
        )
        # 查得到時工具層應回傳 Flex 卡片（合法 JSON），而不是任何查無文案。
        payload_json = json.loads(tool_payload_taipei)
        assert payload_json.get("type") == "flex", "查得到藥局時應回傳 Flex 卡片"
        assert tool_payload_taipei != t("location.type.pharmacy_none").format(
            radius_km="50"
        )
        print("[驗證通過] 工具層回傳的是 Flex 卡片 JSON，而非查無文案。")
    else:
        expected = t("location.type.pharmacy_none").format(radius_km="50")
        assert tool_payload_taipei == expected, (
            "台北車站附近查無藥局時，工具層應回傳 location.type.pharmacy_none 文案"
        )
        print("[附註] 台北車站附近 50 公里內意外查無藥局，已改為驗證查無路徑的文案。")

    # --- 蘭嶼：資料庫內離蘭嶼最近的藥局約 338 公里，遠超過 50 公里上限，
    # 用來驗證「附近真的沒有藥局」時工具層走專屬文案而非通用文案 ---
    lat2, lng2 = LANYU
    result2 = await service.find_nearby_hospitals(lat2, lng2, facility_type="藥局")
    print(f"\n[蘭嶼 {LANYU} + facility_type=藥局]（service 層）結果：")
    _print_facilities(result2.facilities)

    tool_payload_lanyu = await find_nearby_hospitals.ainvoke(
        {"lat": lat2, "lng": lng2, "facility_type": "藥局"}
    )
    print(f"\n[工具層回傳]（完整）：{tool_payload_lanyu}")

    expected_pharmacy_none = t("location.type.pharmacy_none").format(radius_km="50")
    generic_no_facility = t("location.no_facility").format(radius_km="50")

    assert not result2.facilities, "蘭嶼 50 公里內不應查得到藥局（用來驗證查無路徑）"
    assert tool_payload_lanyu == expected_pharmacy_none, (
        "蘭嶼查無藥局時，工具層應回傳 location.type.pharmacy_none 專屬文案"
    )
    assert tool_payload_lanyu != generic_no_facility, (
        "不應誤用通用的 location.no_facility 文案"
    )
    print(
        "[驗證通過] 蘭嶼附近查無藥局時，工具層回傳的是 location.type.pharmacy_none"
        "（藥局專屬文案），而不是 location.no_facility 或 location.department.none。"
    )


async def verify_pharmacy_inventory() -> None:
    """
    額外的主動抽查：直接對資料庫核對藥局收錄量，確認 proposal 記錄的
    「116 家（藥師自營 92 + 藥劑生自營 24）」現在仍然成立 —— 這個數字是
    location.type.pharmacy_none 文案「收錄有限」說法的事實依據，若已經
    偏離太多，文案措辭可能需要一併檢討。
    """
    _print_header("附加抽查：藥局收錄量與 proposal 記錄是否仍相符")
    collection = MongoDBManager.get_medical_collection()
    n_pharmacist = await collection.count_documents({"type": "藥師自營"})
    n_apprentice = await collection.count_documents({"type": "藥劑生自營"})
    total = n_pharmacist + n_apprentice
    print(f"  藥師自營：{n_pharmacist} 家")
    print(f"  藥劑生自營：{n_apprentice} 家")
    print(f"  合計：{total} 家")
    if (n_pharmacist, n_apprentice) == (92, 24):
        print("  → 與 proposal 記錄的 92 + 24 = 116 完全一致。")
    else:
        print(
            "  → 與 proposal 記錄的 92 + 24 = 116 不完全一致"
            "（資料庫內容可能已隨時間變動），僅供參考。"
        )


async def verify_type_distribution_sanity() -> None:
    """
    額外的主動抽查：核對資料庫 distinct type 值是否仍與
    CANONICAL_FACILITY_TYPES（facility_type_matcher.py 硬編的 17 個值）一致。
    若資料庫出現新的 type 值卻沒被納入任何分類，該類院所會被排除在
    所有 facility_type 過濾結果之外而不自知，值得留意。
    """
    _print_header("附加抽查：資料庫 distinct type 值 vs. CANONICAL_FACILITY_TYPES")
    from app.services.medical.facility_type_matcher import CANONICAL_FACILITY_TYPES

    collection = MongoDBManager.get_medical_collection()
    db_types = set(await collection.distinct("type"))
    print(f"  資料庫 distinct type（{len(db_types)} 個）：{sorted(db_types)}")
    print(f"  CANONICAL_FACILITY_TYPES（{len(CANONICAL_FACILITY_TYPES)} 個）：{sorted(CANONICAL_FACILITY_TYPES)}")

    missing_from_code = db_types - CANONICAL_FACILITY_TYPES
    extra_in_code = CANONICAL_FACILITY_TYPES - db_types
    if missing_from_code:
        print(
            f"  [注意] 資料庫存在但程式未歸類的 type 值：{sorted(missing_from_code)}"
            "（這些院所無法被任何 facility_type 過濾條件命中）"
        )
    if extra_in_code:
        print(f"  [注意] 程式列出但資料庫目前查無資料的 type 值：{sorted(extra_in_code)}")
    if not missing_from_code and not extra_in_code:
        print("  → 完全一致，沒有孤兒 type 值。")


async def main() -> None:
    print("正在連線至 MongoDB（唯讀）...")
    MongoDBManager.configure(settings.MONGODB_URI)
    db = MongoDBManager.get_database()
    print(f"已連線，資料庫：{db.name}")

    service = MedicalService()

    await verify_6_2(service)
    await verify_6_3(service)
    await verify_6_4(service)
    await verify_pharmacy_inventory()
    await verify_type_distribution_sanity()

    _print_header("全部驗證完成")
    print("6.2 / 6.3 / 6.4 的 assert 全數通過。")


if __name__ == "__main__":
    asyncio.run(main())
