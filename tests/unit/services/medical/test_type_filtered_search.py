"""
院所類型過濾（facility_type）與既有鄰近搜尋、科別搜尋的接合。

涵蓋：僅類型過濾、類型 × 科別疊加（驗證 $and 結構）、單一條件不含 $and、
類型解析失敗不查 DB、省略 facility_type 時 query 與現狀一致（向後相容）。
"""

import pytest

from app.schemas import MedicalFacility
from app.services.medical.medical_service import (
    MedicalService,
)


def _facility(name: str, distance_meters: float) -> MedicalFacility:
    return MedicalFacility(
        id=f"id-{name}",
        name=name,
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type="醫院",
        departments=["內科"],
        distance_meters=distance_meters,
    )


class FakeRepository:
    """記錄 find_near 收到的參數，並回傳預先安排好的院所。"""

    def __init__(self, facilities: list[MedicalFacility]) -> None:
        self._facilities = facilities
        self.calls: list[dict] = []

    async def find_near(self, lat, lng, radius_meters, limit, query=None):
        self.calls.append(
            {
                "lat": lat,
                "lng": lng,
                "radius_meters": radius_meters,
                "limit": limit,
                "query": query,
            }
        )
        return [f for f in self._facilities if (f.distance_meters or 0) <= radius_meters][
            :limit
        ]


# ---------------------------------------------------------------------------
# find_nearby_hospitals：僅類型過濾
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_nearby_hospitals_applies_type_only_query():
    facilities = [_facility(f"院所{i}", 500 * (i + 1)) for i in range(5)]
    repository = FakeRepository(facilities)
    service = MedicalService(repository=repository)

    result = await service.find_nearby_hospitals(25.0, 121.0, facility_type="醫院")

    assert repository.calls[0]["query"] == {
        "type": {"$in": ["醫院", "綜合醫院", "精神科醫院", "中醫醫院"]}
    }
    assert result.facility_type_match.category == "醫院"
    assert result.facility_type_unresolved is False
    assert len(result.facilities) == 5


@pytest.mark.asyncio
async def test_find_nearby_hospitals_omits_facility_type_matches_status_quo():
    """省略 facility_type 時，query 與行為必須與現狀完全一致（向後相容硬要求）。"""
    facilities = [_facility(f"院所{i}", 500 * (i + 1)) for i in range(5)]
    repository = FakeRepository(facilities)
    service = MedicalService(repository=repository)

    result = await service.find_nearby_hospitals(25.0, 121.0)

    assert len(repository.calls) == 1
    assert repository.calls[0]["query"] is None
    assert result.facility_type_match is None
    assert result.facility_type_unresolved is False


@pytest.mark.asyncio
async def test_find_nearby_hospitals_unresolvable_type_does_not_query_database():
    repository = FakeRepository([_facility("不該回傳", 100)])
    service = MedicalService(repository=repository)

    result = await service.find_nearby_hospitals(
        25.0, 121.0, facility_type="宇宙無敵類型"
    )

    assert repository.calls == []
    assert result.facilities == []
    assert result.facility_type_unresolved is True
    assert result.facility_type_match is None


# ---------------------------------------------------------------------------
# find_nearby_facilities_by_department：類型 × 科別疊加
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_department_and_type_combine_with_and():
    facilities = [_facility(f"院所{i}", 500 * (i + 1)) for i in range(5)]
    repository = FakeRepository(facilities)
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(
        25.0, 121.0, "腸胃科", facility_type="醫院"
    )

    assert repository.calls[0]["query"] == {
        "$and": [
            {"departments": {"$regex": "內科", "$options": "i"}},
            {"type": {"$in": ["醫院", "綜合醫院", "精神科醫院", "中醫醫院"]}},
        ]
    }
    assert result.match.canonical == "內科"
    assert result.facility_type_match.category == "醫院"


@pytest.mark.asyncio
async def test_department_only_query_has_no_and():
    """單一條件（僅科別，未給 facility_type）不得包 $and，比照既有測試斷言。"""
    facilities = [_facility(f"院所{i}", 500 * (i + 1)) for i in range(5)]
    repository = FakeRepository(facilities)
    service = MedicalService(repository=repository)

    await service.find_nearby_facilities_by_department(25.0, 121.0, "腸胃科")

    assert repository.calls[0]["query"] == {
        "departments": {"$regex": "內科", "$options": "i"}
    }


@pytest.mark.asyncio
async def test_type_only_query_via_department_method_has_no_and():
    """理論上 department 一定會存在（必填參數），但同樣驗證單條件不包 $and 這條規則
    本身不受「哪個維度存在」影響——用 _combine_filters 的行為間接驗證。"""
    service = MedicalService(repository=FakeRepository([]))

    combined_both = service._combine_filters(
        {"departments": {"$regex": "內科", "$options": "i"}},
        {"type": {"$in": ["醫院"]}},
    )
    combined_department_only = service._combine_filters(
        {"departments": {"$regex": "內科", "$options": "i"}}, None
    )
    combined_type_only = service._combine_filters(
        None, {"type": {"$in": ["醫院"]}}
    )
    combined_none = service._combine_filters(None, None)

    assert combined_both == {
        "$and": [
            {"departments": {"$regex": "內科", "$options": "i"}},
            {"type": {"$in": ["醫院"]}},
        ]
    }
    assert combined_department_only == {"departments": {"$regex": "內科", "$options": "i"}}
    assert combined_type_only == {"type": {"$in": ["醫院"]}}
    assert combined_none is None


@pytest.mark.asyncio
async def test_department_search_unresolvable_type_does_not_query_database():
    """科別解析成功、但類型解析失敗時，仍不得查 DB（不得靜默退化為不套類型過濾）。"""
    repository = FakeRepository([_facility("不該回傳", 100)])
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(
        25.0, 121.0, "腸胃科", facility_type="宇宙無敵類型"
    )

    assert repository.calls == []
    assert result.facilities == []
    assert result.match.canonical == "內科"
    assert result.facility_type_unresolved is True
    assert result.facility_type_match is None


@pytest.mark.asyncio
async def test_department_search_omits_facility_type_matches_status_quo():
    """省略 facility_type 時，依科別搜尋的 query 與行為必須與現狀完全一致。"""
    facilities = [_facility(f"院所{i}", 500 * (i + 1)) for i in range(5)]
    repository = FakeRepository(facilities)
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, "腸胃科")

    assert repository.calls[0]["query"] == {
        "departments": {"$regex": "內科", "$options": "i"}
    }
    assert result.facility_type_match is None
    assert result.facility_type_unresolved is False


@pytest.mark.asyncio
async def test_unresolvable_department_short_circuits_before_type_is_checked():
    """科別本身就看不懂時，比照既有行為直接回傳，不查 DB——即使 facility_type
    也給了看似合法的值，也不需要額外檢查（既有測試已鎖定此路徑不查 DB）。"""
    repository = FakeRepository([_facility("不該回傳", 100)])
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(
        25.0, 121.0, "宇宙無敵科", facility_type="醫院"
    )

    assert result.match is None
    assert result.facilities == []
    assert repository.calls == []


# ---------------------------------------------------------------------------
# 最終審查 I4：空字串 facility_type 必須等同「沒有給」
#
# facility_type 是選填參數，LLM function calling（尤其 Gemini）對選填字串參數
# 送 "" 是常見行為。舊寫法以 `is not None` 判斷，一個空字串就會走進解析失敗
# 分支：完全不查 DB、直接回「我不確定「」對應到哪一種院所類型」，讓「找附近
# 院所」這個核心流程整個壞掉。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
@pytest.mark.asyncio
async def test_find_nearby_hospitals_treats_blank_facility_type_as_absent(blank):
    facilities = [_facility(f"院所{i}", 500 * (i + 1)) for i in range(5)]
    repository = FakeRepository(facilities)
    service = MedicalService(repository=repository)

    result = await service.find_nearby_hospitals(25.0, 121.0, facility_type=blank)

    assert repository.calls[0]["query"] is None, "空字串不得產生任何 type 過濾條件"
    assert result.facility_type_unresolved is False
    assert result.facility_type_match is None
    assert len(result.facilities) == 5


@pytest.mark.parametrize("blank", ["", "   "])
@pytest.mark.asyncio
async def test_department_search_treats_blank_facility_type_as_absent(blank):
    facilities = [_facility(f"院所{i}", 500 * (i + 1)) for i in range(5)]
    repository = FakeRepository(facilities)
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(
        25.0, 121.0, "腸胃科", facility_type=blank
    )

    assert repository.calls[0]["query"] == {
        "departments": {"$regex": "內科", "$options": "i"}
    }, "空字串不得讓科別查詢多包一層 $and"
    assert result.facility_type_unresolved is False
    assert result.facility_type_match is None
    assert len(result.facilities) == 5


@pytest.mark.asyncio
async def test_whitespace_padded_facility_type_still_resolves():
    """順帶確認正規化只吸收空白，不影響有內容的值。"""
    repository = FakeRepository([_facility("院所", 500)])
    service = MedicalService(repository=repository)

    result = await service.find_nearby_hospitals(25.0, 121.0, facility_type="  醫院 ")

    assert result.facility_type_match.category == "醫院"
    assert repository.calls[0]["query"] == {
        "type": {"$in": ["醫院", "綜合醫院", "精神科醫院", "中醫醫院"]}
    }


# --- 「科別＋類型」複合說法的拆解方式（量測後鎖定，勿往錯方向「修正」）---
#
# 「中醫診所」「牙醫診所」既是科別也是類型的複合說法，正確拆解是
# 科別（中醫一般科／牙科）＋ 類型（診所類 10 個 type 值）兩個維度同時成立，
# 而不是窄化成單一 type 值。
#
# 曾有疑慮：這樣會排除設有中醫部／牙科的綜合醫院，與 design.md 決策 2
# 「科別維度涵蓋較廣」的理由方向相反。以 300 個真實院所座標抽樣量測後確認
# 差異為零（中醫 300/300 相同、牙醫 300/300 相同）——被排除的 143 家中醫醫院
# 與 84 家牙醫醫院在任何抽樣點都進不了最近的 5 名。拿掉過濾只會犧牲
# 「使用者明說了診所」的正確性，換不到結果差異。詳見 design.md。


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("department", "facility_type", "expected_department_pattern"),
    [
        ("中醫", "中醫診所", "中醫一般科"),
        ("牙醫", "牙醫診所", "牙科"),
    ],
)
async def test_specialty_clinic_compound_applies_both_dimensions(
    department, facility_type, expected_department_pattern
):
    repository = FakeRepository([])
    service = MedicalService(repository=repository)

    await service.find_nearby_facilities_by_department(
        25.0, 121.0, department, facility_type=facility_type
    )

    query = repository.calls[0]["query"]
    assert "$and" in query, "科別與類型必須同時成立，不可只留一個維度"

    conditions = query["$and"]
    department_condition = next(c for c in conditions if "departments" in c)
    type_condition = next(c for c in conditions if "type" in c)

    assert department_condition["departments"]["$regex"] == expected_department_pattern
    # 類型走的是整個「診所」類別（10 個 type 值），不是窄化成單一 type 值
    assert len(type_condition["type"]["$in"]) == 10
