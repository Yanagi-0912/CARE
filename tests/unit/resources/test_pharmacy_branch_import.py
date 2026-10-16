"""Directory repairs must not create duplicate pharmacies or invented geography."""
import json
from pathlib import Path
import pytest

from scripts.sync_pharmacy_branches import BranchIndex, COLLECTION, address_key, check_unique_targets, coordinates, map_coordinates, match_existing, simple_schedule


def test_store_and_registered_pharmacy_match_even_when_brand_is_not_in_legal_name():
    existing={"_id":"one","name":"士東藥局","address":"臺北市士林區士東路91巷1號1樓","phone":"(02)28746085"}
    branch={"name":"康是美士東藥局","address":"台北市士林區士東路91巷1號","phone":"02-28746085"}
    assert match_existing(branch,BranchIndex([existing])) is existing


def test_shared_hospital_address_does_not_merge_unrelated_medical_store():
    existing={"_id":"one","name":"醫院藥局","address":"臺北市中正區常德街1號","phone":"0223110000"}
    branch={"name":"維康台大西址一店","address":"臺北市中正區常德街1號B1","phone":"02-23718193"}
    assert match_existing(branch,BranchIndex([existing])) is None


def test_shared_service_phone_does_not_merge_a_different_street_number():
    existing={"_id":"one","name":"杏一台中林新輔具展示店","address":"臺中市南屯區大墩十一街439號","phone":"04-23805425"}
    branch={"name":"杏一台中林新店","address":"臺中市南屯區大墩十一街457號","phone":"04-23805425"}
    assert match_existing(branch,BranchIndex([existing])) is None


def test_multi_number_store_matches_its_registered_pharmacy_address():
    existing={"_id":"one","name":"平和藥局","address":"彰化縣溪湖鎮平和里彰水路三段277、279號","phone":"04-8816880"}
    branch={"name":"大樹溪湖彰水","address":"彰化縣溪湖鎮彰水路三段279號","phone":"04-8816880"}
    assert match_existing(branch,BranchIndex([existing])) is existing


def test_house_number_variants_match_without_merging_a_nearby_address():
    existing={"_id":"one","name":"士東藥局","address":"臺北市士林區士東路91巷1-1號","phone":"02-28746085"}
    branch={"name":"康是美士東藥局","address":"台北市士林區士東路91巷1之1號","phone":"02-28746085"}
    assert match_existing(branch,BranchIndex([existing])) is existing


def test_existing_legal_name_variants_choose_the_exact_unique_identity():
    first={"_id":"one","name":"康是美藥局東英店","address":"臺中市東區十甲路450號","phone":"04-22139034"}
    exact={**first,"_id":"two","name":"康是美東英藥局"}
    branch={"name":"康是美東英藥局","address":exact["address"],"phone":exact["phone"]}
    assert match_existing(branch,BranchIndex([first,exact])) is exact


def test_same_name_different_floors_and_phones_remain_separate_branches():
    existing={"_id":"one","name":"維康奇美藥局","address":"台南市永康區正新街67號1F","phone":"06-2814441"}
    branch={"name":"維康奇美藥局","address":"台南市永康區正新街67號2F","phone":"06-2526893"}
    assert match_existing(branch,BranchIndex([existing])) is None


def test_exact_name_and_postal_address_still_match_after_a_phone_change():
    existing={"_id":"one","name":"維康奇美藥局","address":"台南市永康區正新街67號1F","phone":"06-2814441"}
    branch={**existing,"phone":"06-2526893"}
    assert match_existing(branch,BranchIndex([existing])) is existing


def test_conflicting_sources_cannot_overwrite_one_branch_in_the_same_import():
    entries=[{"id":"one","collection":COLLECTION,"existing":None,"update":{"name":"維康奇美藥局","address":address}} for address in ["台南市永康區正新街67號1F","台南市永康區正新街67號2F"]]
    with pytest.raises(ValueError,match="Conflicting source rows"):
        check_unique_targets([],entries)


def test_unique_identity_conflicts_abort_before_any_database_writes():
    existing={"_id":"one","_collection":COLLECTION,"name":"康是美東英藥局","address":"臺中市東區十甲路450號"}
    plan=[{"id":"two","collection":COLLECTION,"existing":None,"update":{"name":existing["name"],"address":existing["address"]}}]
    with pytest.raises(ValueError,match="before writes"):
        check_unique_targets([existing],plan)


def test_untrusted_or_missing_coordinates_remain_missing():
    assert coordinates(None,None) is None
    assert coordinates(0,0) is None
    assert coordinates("nan","121") is None
    assert coordinates(121,25) is None
    assert map_coordinates("https://maps.google.com/maps?q=25.123%2C+121.456&output=embed") == (25.123,121.456)


def test_complex_business_hours_are_not_invented_from_first_time_range():
    assert simple_schedule("週一~週五09:00~21:00；六日休") is None
    assert simple_schedule("09:00~22:00")["sunday"]["slots"] == [{"open":"09:00","close":"22:00"}]


def test_registry_address_variants_and_floors_share_the_same_building_key():
    assert address_key("基隆市安樂區安樂路二段159號1-2F") == address_key("基隆市安樂區安樂路2段159號1樓")
    assert address_key("臺北巿中山區中山北路1段109號") == address_key("台北市中山區中山北路一段109號2樓")


def test_registry_identity_survives_one_floor_registration_being_cancelled():
    doc={"_id":"one","brand":"屈臣氏","name":"屈臣氏基隆門市","address":"基隆市忠一路8號","companyRegistrationIds":["001","002"]}
    index=BranchIndex([doc])
    assert index.by_source[("屈臣氏","002")] is doc


def test_directory_keeps_store_types_and_unavailable_sources_explicit():
    artifact=json.loads((Path(__file__).resolve().parents[3]/"resources/pharmacy_branches.json").read_text())
    rows=artifact["branches"]
    assert len({(r["brand"],r["sourceId"]) for r in rows}) == len(rows)
    for row in rows:
        assert row["sourceUrl"].startswith("https://")
        assert row["address"] and row["name"]
        if "latitude" in row:
            assert coordinates(row["latitude"],row["longitude"])
    keelung=[r for r in rows if r["brand"]=="康是美" and r["address"].startswith("基隆市")]
    assert len(keelung)==6
    assert all(r["type"]=="藥妝門市" for r in keelung)
    assert artifact["coverage"]["屈臣氏"]["status"]=="registered_branches"
    registered=[r for r in rows if r.get("sourceType")=="company_registration"]
    assert registered
    assert all(r["operationalStatusVerified"] is False for r in registered)
