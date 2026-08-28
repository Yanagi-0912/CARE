#!/usr/bin/env python3
"""Seed a disposable family for manual RBAC end-to-end testing.

建立一個測試家庭（OWNER 與三種角色，外加一位非家庭成員），讓人工 E2E 可以
用真實 HTTP 請求驗證 family-rbac 的授權行為。

**這支腳本只寫測試資料，不碰任何 production 授權行為，也不新增 API。**

安全預設值（重要）
------------------
連線資訊**刻意不從 .env 的 MONGODB_URI／MONGODB_DB 讀取**，而是預設連本機
的 ``mongodb://localhost:27017`` 與 ``CARE_e2e``。理由很直接：這支腳本會寫入
與刪除資料，若預設沿用 .env，一次手滑就把測試資料灌進正式資料庫。要指向別處
請用 ``--mongodb-uri`` / ``--mongodb-db``，或設 ``E2E_MONGODB_URI`` /
``E2E_MONGODB_DB``。

另有一道防呆：解析出的資料庫名稱若與 .env 的 ``MONGODB_DB`` 相同，腳本會直接
中止，除非明確加上 ``--allow-shared-db``。

資料形狀（方向 B 的關鍵）
-------------------------
角色存在**被存取者**的族譜裡。因此：

* OWNER 的族譜帶三位成員，各自有 ``family_role``，並由 ``--state`` 控制
  ``rbac_migration_state``。
* 三位成員各自的族譜**反向**帶著 OWNER，且**不帶 family_role**——角色是單向
  的，受邀者從未表示要授予長輩任何權限。少了這些反向文件，成員打
  ``GET /api/family/me`` 會看到空族譜（那支回的是呼叫者自己的樹）。

用法
----
    python scripts/seed_rbac_e2e.py --reset --state enforced
    python scripts/seed_rbac_e2e.py --state shadow          # 切回影子模式
    python scripts/seed_rbac_e2e.py --dry-run               # 不連線，只印文件
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ── 測試帳號 ────────────────────────────────────────────────────────────
#
# 前綴統一為 U_E2E_，--reset 就是靠它精準清除，不會碰到任何其他資料。
PREFIX = "U_E2E_"
OWNER = f"{PREFIX}OWNER"
GUARDIAN = f"{PREFIX}GUARDIAN"
CAREGIVER = f"{PREFIX}CAREGIVER"
MEMBER = f"{PREFIX}MEMBER"
STRANGER = f"{PREFIX}STRANGER"

ALL_USERS = [OWNER, GUARDIAN, CAREGIVER, MEMBER, STRANGER]

# 角色只掛在 OWNER 的族譜上。
FAMILY_ROLES = {
    GUARDIAN: "GUARDIAN",
    CAREGIVER: "CAREGIVER",
    MEMBER: "MEMBER",
}

DISPLAY_NAMES = {
    OWNER: "E2E 阿公",
    GUARDIAN: "E2E 女兒",
    CAREGIVER: "E2E 看護",
    MEMBER: "E2E 表哥",
    STRANGER: "E2E 路人",
}

# 固定 id，讓 --reset 能精準刪除，也讓 curl 清單可以寫死路徑。
MEDICATION_ID = "e2e-medication-1"
REMINDER_ID = "e2e-reminder-1"

DEFAULT_URI = "mongodb://localhost:27017"
DEFAULT_DB = "CARE_e2e"


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ── 文件建構 ────────────────────────────────────────────────────────────


def build_user(line_id: str) -> Dict[str, Any]:
    """一份完整的 UserProfile 文件。

    健康欄位都填實值，這樣才看得出 MEMBER 被遮蔽之後少了什麼。
    ``role`` 與 ``settings`` 也填——它們刻意未登記分類，跨使用者讀取時
    應該完全不出現在回應裡。
    """
    now = _now()
    is_owner = line_id == OWNER
    return {
        "line_id": line_id,
        "name": DISPLAY_NAMES[line_id],
        "gender": "male" if is_owner else "female",
        "height": 165.0 if is_owner else 160.0,
        "weight": 60.0 if is_owner else 52.0,
        "age": 82 if is_owner else 45,
        "chronic_diseases": ["hypertension", "diabetes"] if is_owner else [],
        "chronic_custom": ["痛風"] if is_owner else [],
        "major_illness_history": "2019 年心導管手術" if is_owner else "",
        "surgery_history": "" if not is_owner else "冠狀動脈支架置放",
        "role": "user",
        "picture_url": f"https://example.invalid/{line_id}.png",
        "settings": {
            "language": "zh-TW",
            "font_size": "xlarge",
            "high_contrast": True,
            "notify_reminder": True,
            "notify_family": True,
            "voice_reply_enabled": False,
            "voice_rate": "normal",
            "voice_gender": "female",
        },
        "created_at": now,
        "updated_at": now,
    }


def build_owner_tree(state: str) -> Dict[str, Any]:
    """OWNER 的族譜：帶三位成員與各自的角色，並掛上遷移狀態。

    ``rbac_migration_state`` 只需要設在這裡——授權判定讀的是**目標擁有者**的
    狀態，不是操作者的。
    """
    now = _now()
    return {
        "user_id": OWNER,
        "family_members": [
            {
                "user_id": member_id,
                "relationship_type": None,
                "display_name": DISPLAY_NAMES[member_id],
                "picture_url": f"https://example.invalid/{member_id}.png",
                "is_care_recipient": False,
                "family_role": role,
            }
            for member_id, role in FAMILY_ROLES.items()
        ],
        "rbac_migration_state": state,
        "created_at": now,
        "updated_at": now,
    }


def build_reverse_tree(member_id: str, state: str) -> Dict[str, Any]:
    """成員自己的族譜：反向帶著 OWNER，**不帶 family_role**。

    角色在這個模型裡是單向的。受邀者從未表示要授予長輩任何權限，因此這一邊
    維持未設定（授權上即 MEMBER）。

    ``rbac_migration_state`` 跟著 ``--state`` 走，與 OWNER 的族譜一致。

    這裡原本寫死 shadow，理由是「這些人的資料不是本次測試的目標」——那個判斷
    是錯的。它讓 ``--state enforced`` 名不副實：長輩對三位家人的角色都是
    MEMBER，卻因為對方的族譜還在影子模式而讀得到全部資料，看起來像授權破洞，
    實際上是 fixture 沒有真的被強制。要測混合狀態，seed 完再單獨改某一棵樹，
    不要讓旗標的語意有例外。
    """
    now = _now()
    return {
        "user_id": member_id,
        "family_members": [
            {
                "user_id": OWNER,
                "relationship_type": None,
                "display_name": DISPLAY_NAMES[OWNER],
                "picture_url": f"https://example.invalid/{OWNER}.png",
                "is_care_recipient": True,
                # family_role 刻意缺席
            }
        ],
        "rbac_migration_state": state,
        "created_at": now,
        "updated_at": now,
    }


def build_stranger_tree(state: str) -> Dict[str, Any]:
    """非家庭成員：有自己的族譜但裡面沒有 OWNER，也不在 OWNER 的族譜裡。"""
    now = _now()
    return {
        "user_id": STRANGER,
        "family_members": [],
        "rbac_migration_state": state,
        "created_at": now,
        "updated_at": now,
    }


def build_medication() -> Dict[str, Any]:
    """一顆帶適應症的藥。

    ``_id`` 是**字串**，不是 ObjectId——`MedicationReminder.id` 與
    `Medication.id` 的型別是 str，repository 寫入時也是 `str(ObjectId())`。
    塞 ObjectId 會在讀取時炸在 Pydantic 驗證。

    `indication` 是藥袋上讀到的適應症，登記為 SENSITIVE。MEMBER 讀這位長輩的
    用藥時，藥名與時段照常出現、這個欄位會是 null——那就是混合分類遮蔽的
    現場證據。

    `spc_indication` / `spc_indication_summary` 也填了，但**讀取時會被覆寫**：
    MedicationService 依證號就地解析仿單，而 `resources/drug_indications.json`
    在本機通常不存在，因此實測多半是 null。要驗遮蔽請看 `indication`。
    """
    now = _now()
    return {
        "_id": MEDICATION_ID,
        "user_id": OWNER,
        "created_by_user_id": MEMBER,
        "name": "Metformin 500mg",
        "generic_name": "Metformin",
        "license_number": "衛署藥製字第012345號",
        "shape": "圓形",
        "color": "白色",
        "score_line": "一字型",
        "mark_one": "E2E 001",
        "mark_two": "",
        "size": "9",
        "thumbnail_url": None,
        "spc_indication": "第二型糖尿病",
        "spc_indication_summary": "控制血糖",
        "unit_content": "500mg",
        "total_quantity": 30,
        "usage_raw": "每日一次，早餐後服用",
        "frequency_code": "QD",
        "indication": "糖尿病",
        "source": "manual",
        "start_date": date.today().isoformat(),
        "end_date": None,
        "enabled": True,
        "created_at": now,
        "updated_at": now,
    }


def build_reminder() -> Dict[str, Any]:
    """一筆用藥提醒。

    ``creator_user_id`` **刻意設成 MEMBER**：變更前，建立者可以永遠修改自己
    建立的提醒。現在授權的對象是該提醒的**用藥者**（OWNER），因此 MEMBER 用
    `PUT /api/medications/reminders/{id}` 會拿到 403——那是「creator 後門已封」
    最直接的證據。
    """
    now = _now()
    return {
        "_id": REMINDER_ID,
        "creator_user_id": MEMBER,
        "user_id": OWNER,
        "slot_type": "morning",
        "scheduled_time": "08:00",
        "start_date": date.today().isoformat(),
        "end_date": None,
        "enabled": True,
        "medication_ids": [MEDICATION_ID],
        "created_at": now,
        "updated_at": now,
    }


def build_summaries() -> List[Dict[str, Any]]:
    """OWNER 的兩筆對話摘要（PRIVATE）。

    CAREGIVER 與 MEMBER 對 PRIVATE 沒有存取權，讀這些會拿到 403——而且授權
    擋在讀取之前，資料不會被撈出來。
    """
    now = _now()
    today = date.today()
    return [
        {
            "line_id": OWNER,
            "summary_date": (today - timedelta(days=offset)).isoformat(),
            "summary": summary_text,
            "language": "zh-TW",
            "created_at": now,
        }
        for offset, summary_text in enumerate(
            [
                "今天問了血壓藥可不可以配葡萄柚汁。",
                "昨天說晚上睡不著，想知道能不能自己買安眠藥。",
            ]
        )
    ]


# ── 清除 ────────────────────────────────────────────────────────────────

# --reset 的刪除條件。每一條都鎖在 U_E2E_ 前綴或固定的 e2e- id 上，
# 不可能誤刪其他資料。
RESET_FILTERS = {
    "users": {"line_id": {"$regex": f"^{PREFIX}"}},
    "family_trees": {"user_id": {"$regex": f"^{PREFIX}"}},
    "medications": {"user_id": {"$regex": f"^{PREFIX}"}},
    "medication_reminders": {
        "$or": [
            {"user_id": {"$regex": f"^{PREFIX}"}},
            {"creator_user_id": {"$regex": f"^{PREFIX}"}},
        ]
    },
    "consultation_summaries": {"line_id": {"$regex": f"^{PREFIX}"}},
    "family_delegations": {
        "$or": [
            {"owner_id": {"$regex": f"^{PREFIX}"}},
            {"delegate_user_id": {"$regex": f"^{PREFIX}"}},
        ]
    },
    "family_role_audit": {"owner_id": {"$regex": f"^{PREFIX}"}},
}


def reset(db) -> None:
    for collection_name, condition in RESET_FILTERS.items():
        result = db[collection_name].delete_many(condition)
        print(f"  reset {collection_name}: deleted={result.deleted_count}")


# ── 寫入 ────────────────────────────────────────────────────────────────


def seed(db, state: str) -> None:
    for line_id in ALL_USERS:
        db["users"].replace_one(
            {"line_id": line_id}, build_user(line_id), upsert=True
        )
    print(f"  users: {len(ALL_USERS)} upserted")

    trees = [build_owner_tree(state)]
    trees += [build_reverse_tree(m, state) for m in FAMILY_ROLES]
    trees.append(build_stranger_tree(state))
    for tree in trees:
        db["family_trees"].replace_one(
            {"user_id": tree["user_id"]}, tree, upsert=True
        )
    print(f"  family_trees: {len(trees)} upserted (owner state={state})")

    db["medications"].replace_one(
        {"_id": MEDICATION_ID}, build_medication(), upsert=True
    )
    db["medication_reminders"].replace_one(
        {"_id": REMINDER_ID}, build_reminder(), upsert=True
    )
    print("  medications / medication_reminders: 1 each")

    db["consultation_summaries"].delete_many({"line_id": OWNER})
    db["consultation_summaries"].insert_many(build_summaries())
    print("  consultation_summaries: 2 inserted")


# ── Token ───────────────────────────────────────────────────────────────


def issue_tokens() -> Dict[str, str]:
    """為每個測試帳號簽一個應用內 JWT。

    直接用 production 的 `AppJwtService`，因此簽出來的 token 與 LIFF 登入拿到
    的完全同一種——`get_current_user` 只驗簽章與 issuer，不回 LINE 查任何
    東西，所以測試帳號不需要真的 LINE 帳號。
    """
    sys.path.insert(0, str(PROJECT_ROOT))
    from app.core.config import settings
    from app.services.liff.jwt_service import AppJwtService

    service = AppJwtService(
        secret=settings.AUTH_JWT_SECRET,
        algorithm=settings.AUTH_JWT_ALGORITHM,
        expires_minutes=settings.AUTH_JWT_EXPIRES_MINUTES,
    )
    return {user: service.issue_for_user(user)[0] for user in ALL_USERS}


# ── curl 清單 ───────────────────────────────────────────────────────────

CURL_HEADER = """#!/usr/bin/env bash
# 由 scripts/seed_rbac_e2e.py 產生——請勿手動編輯，重跑 seed 會覆寫。
#
# 家庭狀態：{state}
# 產生時間：{generated_at}
#
# 每一組都對同一個目標（{owner}）發出請求，只換 Authorization。
# 每行前面的註解是**預期**的 HTTP status 與該看的回應欄位。
#
# 逐條執行，或直接 bash 這個檔案看整體結果。
set -u
BASE="${{BASE:-http://localhost:8000}}"

OWNER_TOKEN="{owner_token}"
GUARDIAN_TOKEN="{guardian_token}"
CAREGIVER_TOKEN="{caregiver_token}"
MEMBER_TOKEN="{member_token}"
STRANGER_TOKEN="{stranger_token}"

OWNER_ID="{owner}"
REMINDER_ID="{reminder_id}"

# 印出 status 與 body，方便逐條核對
call() {{
  local label="$1"; shift
  echo "----- $label"
  curl -s -o /tmp/e2e_body.json -w 'HTTP %{{http_code}}\\n' "$@"
  head -c 600 /tmp/e2e_body.json; echo; echo
}}
"""

CURL_BODY = """
# ═══════════════════════════════════════════════════════════════════════
# 1. GET /api/profiles/{OWNER}
#    重點：MEMBER 拿到的是 200 + 遮蔽，不是 403。回 403 會讓前端誤以為
#    連這個人是誰都不能知道，但族譜清單上明明顯示著他的名字。
# ═══════════════════════════════════════════════════════════════════════
# 預期 200，完整欄位，且含 role / settings（讀自己不遮蔽）
call "profiles OWNER(self)"    -H "Authorization: Bearer $OWNER_TOKEN"     "$BASE/api/profiles/$OWNER_ID"
# 預期 200，含 age / chronic_diseases；不含 role / settings
call "profiles GUARDIAN"       -H "Authorization: Bearer $GUARDIAN_TOKEN"  "$BASE/api/profiles/$OWNER_ID"
# 預期 200，含 age / chronic_diseases（CAREGIVER 的 SENSITIVE 是 Read）
call "profiles CAREGIVER"      -H "Authorization: Bearer $CAREGIVER_TOKEN" "$BASE/api/profiles/$OWNER_ID"
# 預期 200，**只剩** line_id / name / picture_url
call "profiles MEMBER"         -H "Authorization: Bearer $MEMBER_TOKEN"    "$BASE/api/profiles/$OWNER_ID"
# 預期 403，detail 含「權限不足」
call "profiles STRANGER"       -H "Authorization: Bearer $STRANGER_TOKEN"  "$BASE/api/profiles/$OWNER_ID"


# ═══════════════════════════════════════════════════════════════════════
# 2. PUT /api/profiles/{OWNER}   代理寫入（本 change 新增的能力）
#    重點：這條路徑導入前不存在，因此**不受影子模式放寬**——把家庭切回
#    shadow 再跑一次，MEMBER 仍然是 403。
# ═══════════════════════════════════════════════════════════════════════
# 送出去的 body 一律維持**純 ASCII**。
# 這不是美觀問題：Windows 主控台的預設編碼是 CP950，非 ASCII 字元一旦被轉碼
# 就不再是合法的 UTF-8，FastAPI 的 request.json() 會拋 UnicodeDecodeError。
# 那不是 JSONDecodeError，因此不會回 422，而是回
# 「400 There was an error parsing the body」——那個狀態碼在授權測試裡本來
# 就會出現（OWNER 對自己代理寫入正是 400），非常容易被誤讀成授權結果。
# 註解可以用中文，送上線的 body 不行。
PROFILE_BODY='{{"name":"SHOULD-NOT-BE-WRITTEN","gender":"male","height":166,"weight":61,"age":83,"chronic_diseases":["hypertension"],"chronic_custom":[],"major_illness_history":"","surgery_history":""}}'

# 預期 400：改自己的資料請用 PUT /api/profiles/me/update
call "proxy-write OWNER(self)" -X PUT -H "Authorization: Bearer $OWNER_TOKEN"     -H 'Content-Type: application/json' -d "$PROFILE_BODY" "$BASE/api/profiles/$OWNER_ID"
# 預期 200，回應的 skipped_fields 含 "name"（顯示名稱不歸代理寫入管）
call "proxy-write GUARDIAN"    -X PUT -H "Authorization: Bearer $GUARDIAN_TOKEN"  -H 'Content-Type: application/json' -d "$PROFILE_BODY" "$BASE/api/profiles/$OWNER_ID"
# 預期 403：CAREGIVER 對 SENSITIVE 只有 Read
call "proxy-write CAREGIVER"   -X PUT -H "Authorization: Bearer $CAREGIVER_TOKEN" -H 'Content-Type: application/json' -d "$PROFILE_BODY" "$BASE/api/profiles/$OWNER_ID"
# 預期 403
call "proxy-write MEMBER"      -X PUT -H "Authorization: Bearer $MEMBER_TOKEN"    -H 'Content-Type: application/json' -d "$PROFILE_BODY" "$BASE/api/profiles/$OWNER_ID"
# 預期 403
call "proxy-write STRANGER"    -X PUT -H "Authorization: Bearer $STRANGER_TOKEN"  -H 'Content-Type: application/json' -d "$PROFILE_BODY" "$BASE/api/profiles/$OWNER_ID"


# ═══════════════════════════════════════════════════════════════════════
# 3. GET /api/medications/reminders?target_user_id={OWNER}
#    混合分類端點：用藥是 GENERAL、適應症是 SENSITIVE。
#    重點：MEMBER 是 200 而不是 403，但 medications[0].indication 為 null。
#    （spc_indication 讀取時由後端就地解析，本機沒有 drug_indications.json
#      時各角色都會是 null——要驗遮蔽請看 indication）
# ═══════════════════════════════════════════════════════════════════════
# 預期 200，indication = "糖尿病"
call "reminders OWNER(self)"   -H "Authorization: Bearer $OWNER_TOKEN"     "$BASE/api/medications/reminders"
# 預期 200，indication = "糖尿病"
call "reminders GUARDIAN"      -H "Authorization: Bearer $GUARDIAN_TOKEN"  "$BASE/api/medications/reminders?target_user_id=$OWNER_ID"
# 預期 200，indication = "糖尿病"
call "reminders CAREGIVER"     -H "Authorization: Bearer $CAREGIVER_TOKEN" "$BASE/api/medications/reminders?target_user_id=$OWNER_ID"
# 預期 200，藥名與時段照常，**indication 為 null**
call "reminders MEMBER"        -H "Authorization: Bearer $MEMBER_TOKEN"    "$BASE/api/medications/reminders?target_user_id=$OWNER_ID"
# 預期 403
call "reminders STRANGER"      -H "Authorization: Bearer $STRANGER_TOKEN"  "$BASE/api/medications/reminders?target_user_id=$OWNER_ID"


# ═══════════════════════════════════════════════════════════════════════
# 4. PUT /api/medications/reminders/{{id}}   creator 後門
#    這筆提醒的 creator_user_id 正是 MEMBER。變更前他可以永遠改它；
#    現在授權的對象是**用藥者**（OWNER），所以他會被擋。
# ═══════════════════════════════════════════════════════════════════════
TOGGLE='{{"enabled": false}}'

# 預期 200（用藥者本人對自己資料是 OWNER）
call "update-reminder OWNER"     -X PUT -H "Authorization: Bearer $OWNER_TOKEN"     -H 'Content-Type: application/json' -d "$TOGGLE" "$BASE/api/medications/reminders/$REMINDER_ID"
# 預期 200
call "update-reminder GUARDIAN"  -X PUT -H "Authorization: Bearer $GUARDIAN_TOKEN"  -H 'Content-Type: application/json' -d "$TOGGLE" "$BASE/api/medications/reminders/$REMINDER_ID"
# 預期 200（CAREGIVER 的 GENERAL 是 Read/Write）
call "update-reminder CAREGIVER" -X PUT -H "Authorization: Bearer $CAREGIVER_TOKEN" -H 'Content-Type: application/json' -d "$TOGGLE" "$BASE/api/medications/reminders/$REMINDER_ID"
# 預期 403 ← **creator 後門已封的直接證據**
call "update-reminder MEMBER"    -X PUT -H "Authorization: Bearer $MEMBER_TOKEN"    -H 'Content-Type: application/json' -d "$TOGGLE" "$BASE/api/medications/reminders/$REMINDER_ID"
# 預期 403
call "update-reminder STRANGER"  -X PUT -H "Authorization: Bearer $STRANGER_TOKEN"  -H 'Content-Type: application/json' -d "$TOGGLE" "$BASE/api/medications/reminders/$REMINDER_ID"


# ═══════════════════════════════════════════════════════════════════════
# 5. GET /api/consultations/{OWNER}/allsummaries   （PRIVATE）
#    重點：只有 OWNER 與 GUARDIAN 讀得到。CAREGIVER 收得到安全通報，
#    但那不代表他看得到對話紀錄——通知政策與讀取權是分開的。
# ═══════════════════════════════════════════════════════════════════════
# 預期 200，兩筆摘要
call "summaries OWNER(self)"   -H "Authorization: Bearer $OWNER_TOKEN"     "$BASE/api/consultations/me/allsummaries"
# 預期 200，兩筆摘要
call "summaries GUARDIAN"      -H "Authorization: Bearer $GUARDIAN_TOKEN"  "$BASE/api/consultations/$OWNER_ID/allsummaries"
# 預期 403
call "summaries CAREGIVER"     -H "Authorization: Bearer $CAREGIVER_TOKEN" "$BASE/api/consultations/$OWNER_ID/allsummaries"
# 預期 403
call "summaries MEMBER"        -H "Authorization: Bearer $MEMBER_TOKEN"    "$BASE/api/consultations/$OWNER_ID/allsummaries"
# 預期 403
call "summaries STRANGER"      -H "Authorization: Bearer $STRANGER_TOKEN"  "$BASE/api/consultations/$OWNER_ID/allsummaries"


# ═══════════════════════════════════════════════════════════════════════
# 6. GET /api/consultations/{OWNER}/messages/raw   （PRIVATE，走 Redis）
#    授權擋在讀 Redis 之前——最敏感的資料不該有「先撈出來再說」的路徑。
#    Redis 沒有資料時是 200 + messages: []，授權行為一樣驗得到。
# ═══════════════════════════════════════════════════════════════════════
# 預期 200
call "raw OWNER(self)"         -H "Authorization: Bearer $OWNER_TOKEN"     "$BASE/api/consultations/me/messages/raw"
# 預期 200
call "raw GUARDIAN"            -H "Authorization: Bearer $GUARDIAN_TOKEN"  "$BASE/api/consultations/$OWNER_ID/messages/raw"
# 預期 403
call "raw CAREGIVER"           -H "Authorization: Bearer $CAREGIVER_TOKEN" "$BASE/api/consultations/$OWNER_ID/messages/raw"
# 預期 403
call "raw MEMBER"              -H "Authorization: Bearer $MEMBER_TOKEN"    "$BASE/api/consultations/$OWNER_ID/messages/raw"
# 預期 403
call "raw STRANGER"            -H "Authorization: Bearer $STRANGER_TOKEN"  "$BASE/api/consultations/$OWNER_ID/messages/raw"


# ═══════════════════════════════════════════════════════════════════════
# 7. GET /api/family/me
#    回的是**呼叫者自己的**族譜。兩個方向的角色都給：
#      family_role = 他對我的資料是什麼角色（我可以改）
#      my_role     = 我對他的資料是什麼角色（他決定，我不能改）
#    my_permissions 已套用對方的遷移狀態，是實際生效的權限。
# ═══════════════════════════════════════════════════════════════════════
# 預期 200，三位成員，各帶 family_role；role_assignment.is_complete = true
call "family/me OWNER"         -H "Authorization: Bearer $OWNER_TOKEN"     "$BASE/api/family/me"
# 預期 200，一位成員(OWNER)，my_role="GUARDIAN"，
#           my_permissions.sensitive=["READ","WRITE"]、private=["READ"]
call "family/me GUARDIAN"      -H "Authorization: Bearer $GUARDIAN_TOKEN"  "$BASE/api/family/me"
# 預期 200，my_role="CAREGIVER"，sensitive=["READ"]、private=[]
call "family/me CAREGIVER"     -H "Authorization: Bearer $CAREGIVER_TOKEN" "$BASE/api/family/me"
# 預期 200，my_role="MEMBER"，general=["READ"]、sensitive=[]、private=[]
call "family/me MEMBER"        -H "Authorization: Bearer $MEMBER_TOKEN"    "$BASE/api/family/me"
# 預期 200，family_members 為空陣列
call "family/me STRANGER"      -H "Authorization: Bearer $STRANGER_TOKEN"  "$BASE/api/family/me"


# ═══════════════════════════════════════════════════════════════════════
# 對照組：把家庭切回影子模式再跑一次
#   python scripts/seed_rbac_e2e.py --state shadow
#
# 預期差異：
#   * 上面所有 403 會變成 200（第 2 節的代理寫入除外——那是新增的能力，
#     不受影子模式放寬，MEMBER/CAREGIVER 仍是 403）
#   * MEMBER 的 profiles 會回完整健康欄位、reminders 的 indication 會出現
#     （遮蔽也是一種收緊，影子模式下不生效）
#   * family/me 的 my_permissions 會變成 legacy 的寬鬆值
# ═══════════════════════════════════════════════════════════════════════
"""


def build_curl_script(tokens: Dict[str, str], state: str) -> str:
    """組出 curl 清單的內容。

    **兩段模板都必須經過 `.format()`。** 它們是同一種東西（Python format
    string），因此都用 `{{` 表示「輸出一個 `{`」。曾經只有 HEADER 走 format、
    BODY 走 `.replace("{OWNER}", ...)`，於是 BODY 裡的 `{{"name": ...}}` 原封
    不動地輸出成 `{{"name": ...}}`——那不是合法 JSON，FastAPI 一律回
    「There was an error parsing the body」。

    兩段長得一模一樣卻只有一段被 format，是這個 bug 唯一的成因。抽成同一支
    函式並讓兩段走同一條路徑，就沒有「哪一段要不要跳脫」這個問題了。
    """
    return CURL_HEADER.format(
        state=state,
        generated_at=_now().isoformat(timespec="seconds"),
        owner=OWNER,
        reminder_id=REMINDER_ID,
        owner_token=tokens[OWNER],
        guardian_token=tokens[GUARDIAN],
        caregiver_token=tokens[CAREGIVER],
        member_token=tokens[MEMBER],
        stranger_token=tokens[STRANGER],
    ) + CURL_BODY.format(OWNER=OWNER)


def write_curl_file(path: Path, tokens: Dict[str, str], state: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # shell script 一律寫成 LF。Windows 的預設會把換行轉成 CRLF，而那個 CR
    # 會黏在每一行最後一個字上——包含 URL 與變數值。有些 bash 會忍下來，
    # 有些會直接報錯，兩種都不該讓測試腳本去賭。
    path.write_text(
        build_curl_script(tokens, state),
        encoding="utf-8",
        newline=chr(10),
    )


# ── CLI ─────────────────────────────────────────────────────────────────


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed a disposable family for manual RBAC E2E testing."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="先清除所有 U_E2E_* 測試資料（只清這些，不碰其他資料）",
    )
    parser.add_argument(
        "--state",
        choices=["shadow", "enforced"],
        default="enforced",
        help="OWNER 家庭的 rbac_migration_state（預設 enforced）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="不連線資料庫，只印出將寫入的文件與 token",
    )
    parser.add_argument(
        "--mongodb-uri",
        default=os.getenv("E2E_MONGODB_URI", DEFAULT_URI),
        help=f"預設 {DEFAULT_URI}（刻意不讀 .env 的 MONGODB_URI）",
    )
    parser.add_argument(
        "--mongodb-db",
        default=os.getenv("E2E_MONGODB_DB", DEFAULT_DB),
        help=f"預設 {DEFAULT_DB}（刻意不讀 .env 的 MONGODB_DB）",
    )
    parser.add_argument(
        "--allow-shared-db",
        action="store_true",
        help="允許使用與 .env MONGODB_DB 同名的資料庫（預設拒絕）",
    )
    parser.add_argument(
        "--curl-out",
        default="scripts/e2e_curls.generated.sh",
        help="產出的 curl 清單路徑",
    )
    parser.add_argument(
        "--no-curl", action="store_true", help="不產生 curl 清單"
    )
    return parser.parse_args(argv)


def guard_against_production_db(db_name: str, allow_shared: bool) -> None:
    """拒絕寫進 .env 指定的那個資料庫。

    這支腳本會 delete_many。若有人把 --mongodb-db 填成正式庫，--reset 就會在
    正式資料上執行——即使條件鎖著 U_E2E_ 前綴，那也不是應該發生的事。
    """
    configured = (os.getenv("MONGODB_DB") or "").strip()
    if configured and db_name == configured and not allow_shared:
        print(
            f"拒絕執行：目標資料庫 '{db_name}' 與 .env 的 MONGODB_DB 相同。\n"
            f"這支腳本會寫入並刪除資料，請改用另一個資料庫："
            f"--mongodb-db {DEFAULT_DB}\n"
            f"（確定要共用請加 --allow-shared-db）",
            file=sys.stderr,
        )
        raise SystemExit(2)


def main(argv=None) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args(argv)

    print(f"target: {args.mongodb_uri} / {args.mongodb_db}")
    print(f"state : {args.state}")

    if args.dry_run:
        print("\n--dry-run：不連線，僅列出將寫入的文件\n")
        print(f"  users            : {ALL_USERS}")
        owner_tree = build_owner_tree(args.state)
        print(
            f"  owner tree       : {owner_tree['user_id']} "
            f"state={owner_tree['rbac_migration_state']} "
            f"members="
            + str(
                [
                    (m["user_id"], m["family_role"])
                    for m in owner_tree["family_members"]
                ]
            )
        )
        for member_id in FAMILY_ROLES:
            reverse = build_reverse_tree(member_id, args.state)
            member = reverse["family_members"][0]
            print(
                f"  reverse tree     : {member_id} -> "
                f"{member['user_id']} family_role="
                f"{member.get('family_role', '(absent)')}"
            )
        print(f"  stranger tree    : {STRANGER} members=[]")
        medication = build_medication()
        print(
            f"  medication       : _id={medication['_id']!r} "
            f"({type(medication['_id']).__name__}) "
            f"indication={medication['indication']!r}"
        )
        reminder = build_reminder()
        print(
            f"  reminder         : _id={reminder['_id']!r} "
            f"user_id={reminder['user_id']} "
            f"creator_user_id={reminder['creator_user_id']}"
        )
        print(f"  summaries        : {len(build_summaries())} for {OWNER}")
        print("\n  reset filters:")
        for name, condition in RESET_FILTERS.items():
            print(f"    {name}: {condition}")
    else:
        guard_against_production_db(args.mongodb_db, args.allow_shared_db)

        import pymongo
        from pymongo.errors import PyMongoError

        client = pymongo.MongoClient(args.mongodb_uri, serverSelectionTimeoutMS=5000)
        try:
            # 連不上就早點爆，不要寫到一半才發現
            client.admin.command("ping")
        except PyMongoError as exc:
            print(
                "\n".join(
                    [
                        "",
                        f"無法連線到 MongoDB：{args.mongodb_uri}",
                        f"  {type(exc).__name__}: {exc}",
                        "",
                        "本機請先啟動一個 MongoDB，例如：",
                        "  docker run -d -p 27017:27017 --name care-mongo mongo:7",
                        "或以 --mongodb-uri 指向別處。",
                        "（只想看會寫入什麼，不必連線：--dry-run）",
                    ]
                ),
                file=sys.stderr,
            )
            return 1
        db = client[args.mongodb_db]

        if args.reset:
            print("\nreset:")
            reset(db)

        print("\nseed:")
        seed(db, args.state)

    tokens = issue_tokens()
    print("\ntokens (Authorization: Bearer <token>)：")
    for user, token in tokens.items():
        print(f"\n  {user}\n    {token}")

    if not args.no_curl:
        out = Path(args.curl_out)
        if not out.is_absolute():
            out = PROJECT_ROOT / out
        write_curl_file(out, tokens, args.state)
        print(f"\ncurl 清單已產生：{out}")
        print(f"  bash {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
