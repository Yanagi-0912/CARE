"""E2E seed 腳本產出的 curl 清單。

這裡守的是一個**安靜失敗**的 bug 類型：模板的大括號跳脫寫錯時，產出的 JSON
會變成 `{{"name": ...}}`，bash 語法完全正確、腳本照跑不誤，只有 FastAPI 回
「There was an error parsing the body」——而那個 400 看起來像授權的結果，
很容易被當成 E2E 的正常輸出讀過去。

成因是兩段模板長得一模一樣（都用 `{{` 跳脫），但只有一段真的走
`.format()`。因此這裡不只驗 JSON，也驗兩段都經過同一條產生路徑。
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "seed_rbac_e2e.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("seed_rbac_e2e", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed = _load_module()

FAKE_TOKENS = {user: f"token-for-{user}" for user in seed.ALL_USERS}


@pytest.fixture(scope="module")
def script() -> str:
    return seed.build_curl_script(FAKE_TOKENS, "enforced")


def _shell_single_quoted(script: str, name: str) -> str:
    match = re.search(rf"^{name}='(.*)'$", script, re.M)
    assert match, f"產出的腳本裡找不到 {name}"
    return match.group(1)


@pytest.mark.parametrize("name", ["PROFILE_BODY", "TOGGLE"])
def test_request_bodies_are_valid_json(script, name):
    """送出去的必須是單層 JSON。

    `{{"enabled": false}}` 在 bash 裡是完全合法的字串，錯誤只會出現在
    HTTP 400，而那個狀態碼在授權測試裡本來就會出現——所以要在這裡擋。
    """
    raw = _shell_single_quoted(script, name)
    assert not raw.startswith("{{"), f"{name} 的大括號沒有被跳脫回單層：{raw[:40]}"
    json.loads(raw)  # 不合法就直接拋 JSONDecodeError


@pytest.mark.parametrize("name", ["PROFILE_BODY", "TOGGLE"])
def test_request_bodies_are_ascii_only(script, name):
    """送出去的 body 必須是純 ASCII。

    Windows 主控台的預設編碼是 CP950，非 ASCII 字元一旦被轉碼就不再是合法的
    UTF-8。FastAPI 的 `request.json()` 會拋 UnicodeDecodeError——那**不是**
    JSONDecodeError，所以不會回 422，而是回「400 There was an error parsing
    the body」。而 400 在這份授權測試裡本來就是預期值之一（OWNER 對自己代理
    寫入正是 400），因此這個失敗看起來會像正常結果，極難察覺。

    註解用中文沒問題，只有真的送上線的 body 有這個限制。
    """
    raw = _shell_single_quoted(script, name)
    assert raw.isascii(), f"{name} 含非 ASCII 字元，在 CP950 主控台會被轉碼：{raw}"


def test_profile_body_satisfies_the_request_model(script):
    """代理寫入的 body 要能通過 `UserProfileData` 的必填欄位。

    少一個欄位會得到 422，那同樣會被誤讀成授權結果。
    """
    from app.models.user import UserProfileData

    body = json.loads(_shell_single_quoted(script, "PROFILE_BODY"))
    UserProfileData(**body)


def test_placeholders_are_all_substituted(script):
    """兩段模板都要走 format：任何殘留的 `{OWNER}` 或 `{{` 都是漏掉的證據。"""
    assert "{OWNER}" not in script
    assert "{{" not in script
    assert "}}" not in script


def test_owner_id_reaches_every_section(script):
    """`{OWNER}` 出現在四個章節標題與多條 URL，全部都要被代換。"""
    assert script.count(seed.OWNER) >= 8


def test_curl_expectation_header_is_kept_literal(script):
    """curl 的 `%{http_code}` 必須留成單層，否則進度輸出會壞掉。

    它與 JSON body 用的是同一套跳脫規則——這條是那套規則的反向守門。
    """
    assert "-w 'HTTP %{http_code}\\n'" in script


def test_every_endpoint_covers_five_roles(script):
    """七支端點 × 五種角色 = 35 條。少一條就是有角色沒被驗到。"""
    assert len(re.findall(r"^call ", script, re.M)) == 35


def test_tokens_are_substituted_not_left_as_placeholders(script):
    for user, token in FAKE_TOKENS.items():
        assert token in script, f"{user} 的 token 沒有出現在產出的腳本裡"


@pytest.mark.parametrize("state", ["shadow", "enforced"])
def test_state_is_recorded_in_the_header(state):
    """產出的檔案要說得出它對應的是哪一種遷移狀態。

    兩份輸出長得幾乎一樣，沒有這行就分不出手上的清單是哪一次產生的。
    """
    assert f"家庭狀態：{state}" in seed.build_curl_script(FAKE_TOKENS, state)


def test_reset_filters_are_locked_to_the_test_prefix():
    """`--reset` 會 delete_many，條件必須每一條都鎖在 U_E2E_ 前綴上。"""
    for collection, condition in seed.RESET_FILTERS.items():
        serialized = json.dumps(condition)
        assert f"^{seed.PREFIX}" in serialized, (
            f"{collection} 的刪除條件沒有鎖前綴：{condition}"
        )
        assert serialized.count("$regex") == serialized.count(f"^{seed.PREFIX}"), (
            f"{collection} 有沒鎖前綴的 regex 條件：{condition}"
        )


def test_reverse_trees_carry_no_family_role():
    """角色是單向的：受邀者從未表示要授予長輩任何權限。"""
    for member_id in seed.FAMILY_ROLES:
        member = seed.build_reverse_tree(member_id, "enforced")["family_members"][0]
        assert member["user_id"] == seed.OWNER
        assert "family_role" not in member


def test_owner_tree_carries_roles_and_state():
    tree = seed.build_owner_tree("enforced")
    assert tree["rbac_migration_state"] == "enforced"
    roles = {m["user_id"]: m["family_role"] for m in tree["family_members"]}
    assert roles == seed.FAMILY_ROLES


def test_state_applies_to_every_seeded_tree():
    """``--state`` SHALL 套用到全部族譜，不只 OWNER 那一棵。

    反向族譜原本寫死 shadow。後果是 ``--state enforced`` 名不副實：長輩對三位
    家人的角色都是 MEMBER，卻因為對方的族譜仍在影子模式而讀得到全部健康資料
    與對話摘要。在畫面上那看起來就是授權破洞——實際上是 fixture 沒有真的被
    強制，而這種「像 bug 的假象」比真 bug 更花時間。
    """
    for state in ("shadow", "enforced"):
        trees = [seed.build_owner_tree(state)]
        trees += [seed.build_reverse_tree(m, state) for m in seed.FAMILY_ROLES]
        trees.append(seed.build_stranger_tree(state))
        assert {t["rbac_migration_state"] for t in trees} == {state}


def test_medication_id_is_a_string():
    """Medication.id 的型別是 str；塞 ObjectId 會在讀取時炸在 Pydantic 驗證。"""
    assert isinstance(seed.build_medication()["_id"], str)
    assert isinstance(seed.build_reminder()["_id"], str)


def test_reminder_creator_is_the_member_on_purpose():
    """creator 後門的驗證素材：建立者是 MEMBER，用藥者是 OWNER。"""
    reminder = seed.build_reminder()
    assert reminder["creator_user_id"] == seed.MEMBER
    assert reminder["user_id"] == seed.OWNER


def test_every_seeded_member_has_a_relationship():
    """稱謂 SHALL 填滿，值 SHALL 是前端認得的 key。

    全填 None 時，族譜頁的成員卡片會顯示「未設定」——那三個字與角色管理對話框
    的「尚未設定」幾乎一樣，於是「權限明明設定成功了，畫面卻說未設定」變成一個
    看起來像 bug 的假象，而追一個不存在的問題比追真 bug 更花時間。

    值取自前端的 RELATIONSHIP_LABEL；不在表內的字串會原樣顯示在畫面上。
    """
    known = {"parent", "child", "spouse", "sibling", "grandparent", "grandchild", "other"}

    owner_tree = seed.build_owner_tree("enforced")
    for member in owner_tree["family_members"]:
        assert member["relationship_type"] in known, member

    for member_id in seed.FAMILY_ROLES:
        reverse = seed.build_reverse_tree(member_id, "enforced")
        assert reverse["family_members"][0]["relationship_type"] in known


def test_relationships_point_the_right_way():
    """兩個方向是不同的事實：女兒對阿公是 child，阿公對女兒是 parent。

    共用一份表就會在其中一邊講反話——而族譜頁正是照這個欄位顯示稱謂的。
    """
    assert seed.RELATIONSHIPS[seed.GUARDIAN] == "child"
    assert seed.REVERSE_RELATIONSHIPS[seed.GUARDIAN] == "parent"


# ── 12.1 個人健康紀錄（personal-health-tracking）─────────────────────────


def test_owner_stays_male_with_the_asserted_display_name():
    """驗證腳本斷言 OWNER 之名為「E2E 阿公」——這裡先確認 seed 端沒有變動：
    族譜／性別資料不因為新增健康資料而改變（dispatch notes）。
    """
    assert seed.DISPLAY_NAMES[seed.OWNER] == "E2E 阿公"
    assert seed.build_user(seed.OWNER)["gender"] == "male"


def test_guardian_stays_female():
    """經期紀錄要能記到 GUARDIAN 身上，她的個人健康檔案性別必須是女性
    （menstrual-cycle-log spec「僅女性使用者可建立」）。"""
    assert seed.build_user(seed.GUARDIAN)["gender"] == "female"


def test_alert_thresholds_belong_to_owner():
    thresholds = seed.build_alert_thresholds()
    assert thresholds["user_id"] == seed.OWNER
    assert thresholds["updated_by"] == seed.OWNER
    # 上下限要有實際落差，否則「above_range」的量測情境沒有意義。
    assert thresholds["systolic_high"] > thresholds["systolic_low"]
    assert thresholds["diastolic_high"] > thresholds["diastolic_low"]
    assert thresholds["glucose_fasting_high"] > thresholds["glucose_low"]
    assert thresholds["glucose_nonfasting_high"] > thresholds["glucose_low"]


def test_measurements_use_classify_measurement_not_hand_written_levels():
    """等級 SHALL 用真正的 classify_measurement 對照 OWNER 的提醒範圍算出來
    （dispatch notes：「不再手寫，避免日後跑偏」）。這裡反過來拿同一份門檻
    重新分類一次每一筆量測，確認 seed 存的 level 與重新計算的結果一致。
    """
    from app.models.health import CreateBloodGlucoseRequest, CreateBloodPressureRequest
    from app.services.health.health_level import classify_measurement

    thresholds = seed.build_owner_thresholds()
    measurements = seed.build_measurements()

    for doc in measurements:
        assert doc["user_id"] == seed.OWNER
        if doc["kind"] == "blood_pressure":
            request = CreateBloodPressureRequest(
                systolic=doc["systolic"], diastolic=doc["diastolic"], pulse=doc.get("pulse")
            )
        else:
            request = CreateBloodGlucoseRequest(
                glucose_mg_dl=doc["glucose_mg_dl"], meal_context=doc["meal_context"]
            )
        assert doc["level"] == classify_measurement(request, thresholds)


def test_measurements_include_both_above_and_within_range():
    """至少一筆 above_range、一筆 within_range（dispatch notes 的硬性要求）。"""
    levels = {doc["level"] for doc in seed.build_measurements()}
    assert "above_range" in levels
    assert "within_range" in levels


def test_measurements_are_not_in_the_future():
    """measured_at 一律在過去——CreateBloodPressureRequest／
    CreateBloodGlucoseRequest 只接受不晚於送出當下 5 分鐘以上的時間，seed
    資料不該是連 API 自己都不接受的形狀（dispatch notes）。"""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for doc in seed.build_measurements():
        measured_at = doc["measured_at"]
        if measured_at.tzinfo is None:
            measured_at = measured_at.replace(tzinfo=timezone.utc)
        assert measured_at <= now


def test_measurement_ids_are_strings():
    """同 Medication.id／MedicationReminder.id 的理由：塞 ObjectId 會在讀取
    時炸在 Pydantic 驗證。"""
    for doc in seed.build_measurements():
        assert isinstance(doc["_id"], str)


def test_menstrual_records_belong_to_guardian_not_owner():
    """經期是 PERSONAL 分類，只有 GUARDIAN 本人的資料——OWNER 維持男性，
    不該出現在這份資料裡（dispatch notes）。"""
    records = seed.build_menstrual_records()
    assert len(records) >= 2
    for record in records:
        assert record["user_id"] == seed.GUARDIAN
        assert record["_id"] not in ("", None)


def test_menstrual_records_satisfy_the_create_request_model():
    """seed 資料要能通過 CreateMenstrualRecordRequest 的驗證（日期格式、
    結束不早於開始、間隔不超過 15 天、開始不晚於今天）——同 measurements
    的理由，不該是連 API 自己都不接受的形狀。"""
    from app.models.health import CreateMenstrualRecordRequest

    for record in seed.build_menstrual_records():
        CreateMenstrualRecordRequest(
            start_date=record["start_date"],
            end_date=record.get("end_date"),
            flow=record.get("flow"),
            note=record.get("note"),
        )


def test_menstrual_records_have_distinct_start_dates_so_cycle_length_is_non_null():
    """cycle_length_days 由服務層依「前一筆的開始日期」現算，不落地存資料庫
    ——seed 這裡只要保證至少兩筆、且開始日期不同，讀取時該欄位才有得算
    （dispatch notes：「至少兩筆記錄，這樣 cycle_length_days 才非 null」）。
    """
    records = seed.build_menstrual_records()
    start_dates = [r["start_date"] for r in records]
    assert len(set(start_dates)) == len(start_dates)
    # 序列化的紀錄不落地存計算欄位——同 repository.add 的 dump 方式。
    for record in records:
        assert "cycle_length_days" not in record
        assert "period_length_days" not in record


def test_steps_belong_to_owner_on_taipei_today():
    """OWNER 今天（台北日曆日）的計步工作階段（dispatch notes）。"""
    from datetime import datetime

    from app.models.health import TAIPEI_TZ

    steps = seed.build_steps()
    assert len(steps) >= 1
    taipei_today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    for session in steps:
        assert session["user_id"] == seed.OWNER
        assert session["date"] == taipei_today
        assert session["steps"] > 0


def test_step_session_ids_are_uuid4():
    """session_id 須為前端會送的形狀——真的 UUID v4（router 的路徑參數是
    pydantic.UUID4）。"""
    from uuid import UUID

    for session in seed.build_steps():
        parsed = UUID(session["session_id"], version=4)
        assert str(parsed) == session["session_id"]


def test_reset_filters_cover_the_five_new_collections():
    """12.1 的五個新 collection 都要進 --reset 的清除範圍
    （dispatch notes：「extend it to the five new collections」）。"""
    expected = {
        "health_measurements",
        "health_alert_thresholds",
        "menstrual_records",
        "step_sessions",
        "health_alert_claims",
    }
    assert expected <= set(seed.RESET_FILTERS)
    for collection in expected:
        assert seed.RESET_FILTERS[collection] == {
            "user_id": {"$regex": f"^{seed.PREFIX}"}
        }
