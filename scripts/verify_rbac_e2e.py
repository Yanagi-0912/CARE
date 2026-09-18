#!/usr/bin/env python3
"""驗證 RBAC E2E 的**遮蔽**行為，不只是狀態碼。

狀態碼證明不了這個 change 的核心價值：MEMBER 讀長輩的健康檔案會拿到 200，
但那個 200 裡面**必須**只剩身分識別；讀用藥會拿到 200，但適應症必須是 null。
兩者若沒遮蔽，狀態碼一模一樣，人工看不出差別。

刻意不用 curl + 暫存檔：Windows 上 curl 是原生執行檔，Git Bash 會把
`-o /tmp/x.json` 轉成 `C:\\Users\\...\\AppData\\Local\\Temp\\x.json`，而原生
Python 把 `/tmp/x.json` 解讀成 `C:\\tmp\\x.json`——兩邊指到不同地方，檔案寫得
出來卻讀不到。直接用 urllib 發請求，這個問題就不存在。

token 從 `scripts/e2e_curls.generated.sh` 讀，與人工跑的那份完全一致。

用法：
    python scripts/verify_rbac_e2e.py
    python scripts/verify_rbac_e2e.py --base http://localhost:8000
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CURL_FILE = PROJECT_ROOT / "scripts" / "e2e_curls.generated.sh"

ROLES = ("OWNER", "GUARDIAN", "CAREGIVER", "MEMBER", "STRANGER")

# 健康狀況（SENSITIVE）＋ 刻意未登記的系統欄位。MEMBER 這些一個都不該看到。
HEALTH_AND_SYSTEM_FIELDS = {
    "age",
    "gender",
    "height",
    "weight",
    "chronic_diseases",
    "chronic_custom",
    "major_illness_history",
    "surgery_history",
    "role",
    "settings",
}


def read_var(source: str, name: str, quote: str = '"') -> str:
    match = re.search(rf"^{name}={quote}(.*){quote}$", source, re.M)
    if not match:
        sys.exit(
            f"在 {CURL_FILE.name} 找不到 {name}。\n"
            f"請先重跑：python scripts/seed_rbac_e2e.py --dry-run --state enforced"
        )
    return match.group(1)


def derive_guardian_id(owner_id: str) -> str:
    """由 OWNER_ID 換算出 GUARDIAN 的 id。

    ``seed_rbac_e2e.py`` 的帳號一律是 ``PREFIX + 角色名``（``U_E2E_OWNER``／
    ``U_E2E_GUARDIAN``／…，見該腳本的 ``ALL_USERS``）。curl 清單只帶了
    ``OWNER_ID`` 這一個 id 變數，GUARDIAN 的 id 用同一個命名規則換算即可，
    不必另外改 ``seed_rbac_e2e.py`` 的範本多開一個變數、也不必在這裡重新
    簽一次 token。
    """
    guardian_id = re.sub(r"OWNER$", "GUARDIAN", owner_id)
    assert guardian_id != owner_id, f"無法從 OWNER_ID 換算出 GUARDIAN 的 id：{owner_id!r}"
    return guardian_id


def build_threshold_body() -> str:
    """(f) 用的提醒範圍設定 body。內容本身不重要——CAREGIVER 對提醒範圍只有
    讀取權，這支請求 SHALL 在授權判定就被擋下，不會走到寫入；重要的是這裡
    要送一份 ``UpdateHealthAlertThresholdRequest`` 驗證得過的合法 body，
    避免驗證錯誤的 422 被誤讀成授權結果的 403。"""
    return json.dumps({"systolic_high": 150, "systolic_low": 95})


def build_step_body() -> str:
    """(i) 用的步數同步 body。``started_at`` 用固定的過去時間，不用「跑
    測試當下」算——固定值不受「量測時間不得晚於送出時間 5 分鐘以上」那條
    規則影響，也不會因為測試在午夜前後跑而變成未來時間。內容本身同
    ``build_threshold_body()`` 的理由：這支請求應該在「只能本人回報」那道
    檢查就被擋下，根本不會驗證 body。"""
    return json.dumps({"steps": 100, "started_at": "2024-01-01T00:00:00Z"})


class Client:
    def __init__(self, base: str, tokens: dict):
        self.base = base.rstrip("/")
        self.tokens = tokens

    def call(self, role: str, path: str, method: str = "GET", body: str = None):
        request = urllib.request.Request(self.base + path, method=method)
        request.add_header("Authorization", "Bearer " + self.tokens[role])
        if body is not None:
            request.add_header("Content-Type", "application/json")
            request.data = body.encode("utf-8")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"raw": raw}
        except urllib.error.URLError as exc:
            sys.exit(f"連不上 {self.base}：{exc.reason}\n請先啟動 backend。")


class Report:
    def __init__(self):
        self.ok = True

    def check(self, label: str, passed: bool, detail: str = "") -> None:
        self.ok = self.ok and passed
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {label}" + (f"  {detail}" if detail else ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    args = parser.parse_args()

    source = CURL_FILE.read_text(encoding="utf-8")
    tokens = {role: read_var(source, f"{role}_TOKEN") for role in ROLES}
    owner_id = read_var(source, "OWNER_ID")
    profile_body = read_var(source, "PROFILE_BODY", quote="'")
    guardian_id = derive_guardian_id(owner_id)

    client = Client(args.base, tokens)
    report = Report()

    print(f"target: {args.base}  owner: {owner_id}\n")

    # ── (a) 健康檔案的欄位遮蔽 ────────────────────────────────────────
    status, profile = client.call("MEMBER", f"/api/profiles/{owner_id}")
    report.check("(a) MEMBER 讀 profile 回 200", status == 200, f"HTTP {status}")
    leaked = sorted(set(profile) & HEALTH_AND_SYSTEM_FIELDS) if status == 200 else []
    report.check(
        "(a) MEMBER 看不到健康與系統欄位",
        status == 200 and not leaked,
        f"洩漏={leaked}" if leaked else f"回傳欄位={sorted(profile)}",
    )
    report.check(
        "(a) MEMBER 仍看得到身分",
        status == 200 and "name" in profile,
        f"name={profile.get('name')!r}",
    )

    # ── (b) 適應症遮蔽（含遞迴進 medications）─────────────────────────
    def indication(role: str):
        status, data = client.call(
            role, f"/api/medications/reminders?target_user_id={owner_id}"
        )
        if status != 200:
            return f"<HTTP {status}>"
        if not data or not data[0].get("medications"):
            return "<沒有用藥資料，請確認 seed 有跑>"
        return data[0]["medications"][0].get("indication")

    member_indication = indication("MEMBER")
    caregiver_indication = indication("CAREGIVER")
    report.check(
        "(b) MEMBER 看不到適應症",
        member_indication is None,
        f"indication={member_indication!r}",
    )
    # 對照組。少了它，「全部都是 null」也會假通過。
    report.check(
        "(b) CAREGIVER 看得到適應症",
        caregiver_indication == "糖尿病",
        f"indication={caregiver_indication!r}",
    )

    # ── (c) 代理寫入：該寫的寫了，不該碰的沒碰 ───────────────────────
    put_status, put_body = client.call(
        "GUARDIAN", f"/api/profiles/{owner_id}", "PUT", profile_body
    )
    report.check(
        "(c) GUARDIAN 代理寫入回 200",
        put_status == 200,
        f"HTTP {put_status} {put_body}",
    )
    report.check(
        "(c) skipped_fields 含 name",
        "name" in (put_body.get("skipped_fields") or []),
        f"{put_body.get('skipped_fields')}",
    )

    _, back = client.call("GUARDIAN", f"/api/profiles/{owner_id}")
    report.check("(c) age 確實寫入 (83)", back.get("age") == 83, f"age={back.get('age')!r}")
    report.check(
        "(c) name 未被覆寫",
        back.get("name") != "SHOULD-NOT-BE-WRITTEN",
        f"name={back.get('name')!r}",
    )
    # 這條擋的是 upsert_user_profile 會把 picture_url 寫成 None 的那顆地雷。
    report.check(
        "(c) picture_url 未被清掉",
        bool(back.get("picture_url")),
        f"picture_url={back.get('picture_url')!r}",
    )

    # ── (d)(e) 血壓血糖量測：SENSITIVE，GUARDIAN／CAREGIVER 可讀，MEMBER
    #    不行（app/routers/users/health.py 的 GET /api/health/measurements）
    status, _ = client.call("MEMBER", f"/api/health/measurements?user_id={owner_id}")
    report.check("(d) MEMBER 讀 OWNER 量測 403", status == 403, f"HTTP {status}")

    status, measurements = client.call(
        "CAREGIVER", f"/api/health/measurements?user_id={owner_id}"
    )
    report.check("(e) CAREGIVER 讀 OWNER 量測 200", status == 200, f"HTTP {status}")
    report.check(
        "(e) CAREGIVER 讀到的量測筆數 > 0",
        status == 200 and bool(measurements),
        f"筆數={len(measurements) if status == 200 else '?'}",
    )

    # ── (f) 提醒範圍：SENSITIVE 寫入權依現行矩陣只有 GUARDIAN，CAREGIVER
    #    只有讀取權，PUT 應 403（PUT /api/health/alert-thresholds）
    threshold_body = build_threshold_body()
    status, _ = client.call(
        "CAREGIVER",
        f"/api/health/alert-thresholds?user_id={owner_id}",
        "PUT",
        threshold_body,
    )
    report.check("(f) CAREGIVER 設定 OWNER 提醒範圍 403", status == 403, f"HTTP {status}")

    # ── (g)(h) 經期：PERSONAL 分類，不經家庭矩陣——任何非本人的請求一律
    #    403，即使 OWNER 對 GUARDIAN 的其餘 SENSITIVE 資料有完整讀取權也一樣
    #    （app/routers/users/health.py 的 MENSTRUAL_CROSS_USER_DETAIL）
    status, _ = client.call("OWNER", f"/api/health/menstrual?user_id={guardian_id}")
    report.check("(g) OWNER 讀 GUARDIAN 的經期 403", status == 403, f"HTTP {status}")

    status, menstrual_records = client.call("GUARDIAN", "/api/health/menstrual")
    report.check("(h) GUARDIAN 讀自己的經期 200", status == 200, f"HTTP {status}")
    report.check(
        "(h) GUARDIAN 讀到至少 2 筆經期紀錄",
        status == 200 and len(menstrual_records) >= 2,
        f"筆數={len(menstrual_records) if status == 200 else '?'}",
    )

    # ── (i) 步數：只能本人回報，不支援代為記錄，任何角色（包含 GUARDIAN）
    #    帶他人 user_id 一律 403（PUT /api/health/steps/sessions/{id}）
    #    started_at 用固定的過去時間，不受「跑測試當下」影響。
    step_body = build_step_body()
    session_id = str(uuid.uuid4())
    status, _ = client.call(
        "GUARDIAN",
        f"/api/health/steps/sessions/{session_id}?user_id={owner_id}",
        "PUT",
        step_body,
    )
    report.check("(i) GUARDIAN 代 OWNER 回報步數 403", status == 403, f"HTTP {status}")

    print()
    print("E2E 遮蔽驗證：", "全部通過" if report.ok else "有項目未通過，見上方 FAIL")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
