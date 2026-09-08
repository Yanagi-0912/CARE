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
