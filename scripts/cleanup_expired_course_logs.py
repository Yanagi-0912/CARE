"""清掉「療程早已結束、規則卻照常展開」所留下的空提醒紀錄。

背景
────
處方箋建立提醒時，療程結束日只寫進了 `medications.end_date`，沒有寫進
`medication_reminders`——規則的 `end_date` 在 `find_or_create_reminder` 的
`$setOnInsert` 一律是 None（長期有效）。療程結束後兩邊脫鉤：規則照常每天展開
紀錄並推播，藥品清單卻已全數失效，推出去的是一張說不出要吃什麼的空卡片。

排程器端的修正（`_resolve_suppressed_reminder_ids`）讓「要不要推」改由「今天
還有沒有有效的藥」推導，往後不會再產生這種紀錄，而且會把當日已展開、還沒確認
的紀錄一併作廢。但它管不到過去已經落地的歷史紀錄——那些 `missed` 會出現在
使用者的用藥歷史裡，變成一筆一筆「他漏吃了」的假紀錄。這支腳本就是清那個。

它做什麼
────────
逐筆檢查指定期間內的 log：找出「它的規則掛了藥、但那一天一顆有效的都沒有」
的紀錄，也就是當初推出去必定是空卡片的那些。

  * 預設只讀不寫，印出完整清單供人核對。
  * 帶 `--apply` 才會寫入，把它們的 status 改成 `cancelled`。
    選 `cancelled` 而不是刪除：紀錄本身「這個時段當天確實展開過」是事實，
    而 `list_..._by_user` 的用藥歷史查詢帶 `status != cancelled`，改成
    cancelled 就會從使用者看得到的歷史裡消失，同時保住這筆稽核痕跡。

`taken` 的紀錄一律不動——使用者按過確認就是按過，不論當時卡片上有沒有藥名。

`missed` 的紀錄會被改掉，這一點要清楚：那些時段當初確實送出過家屬逾時警報，
改成 cancelled 之後資料庫與「家屬當時收到的通知」就不再一致。這裡仍然選擇改，
理由是另一邊的代價更大——留著就是在使用者的用藥歷史裡長期掛著一批他其實無藥
可吃的「漏吃」，那是對使用者的不實紀錄。腳本會把 pending 與 missed 的筆數分開
印，決定要不要 `--apply` 之前請先看那個數字。

用法
────
    .venv/bin/python scripts/cleanup_expired_course_logs.py
    .venv/bin/python scripts/cleanup_expired_course_logs.py --user-id U123... --days 30
    .venv/bin/python scripts/cleanup_expired_course_logs.py --days 30 --apply
"""

import argparse
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pymongo
from dotenv import load_dotenv

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
DEFAULT_DB_NAME = "CARE_database"
DEFAULT_DAYS = 30

# 與 app/repositories/medication_repository.py 的 _active_date_window 同語意：
# 欄位缺席、或 end_date 為 null（長期用藥）皆視為不限。這裡是純 Python 的
# 逐筆判斷而不是重用那個 Mongo 查詢條件，因為要對「每一筆 log 各自的日期」
# 判斷，而不是對單一個今天。
def _is_active_on(medication: dict, date_str: str) -> bool:
    if not medication.get("enabled", False):
        return False
    start = medication.get("start_date")
    if start and start > date_str:
        return False
    end = medication.get("end_date")
    if end and end < date_str:
        return False
    return True


def _get_mongodb_url() -> str:
    url = os.getenv("MONGODB_URL") or os.getenv("MONGODB_URI")
    if not url:
        raise RuntimeError("Missing MONGODB_URL or MONGODB_URI")
    return url


def _taipei_date_str(dt: datetime) -> str:
    """把資料庫取回的 scheduled_at 換算成台北日期。

    pymongo 未啟用 tz_aware，時間以 naive UTC 讀回；不先補上 UTC 再轉換，
    早上 08:00 的紀錄會被算成前一天，日期判斷整個位移一天。
    """
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d")


def _fmt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")


def find_empty_card_logs(db, user_filter: dict[str, Any], since: datetime) -> list[dict]:
    """找出「規則掛了藥、但當天一顆有效的都沒有」的 log。

    `medication_ids` 為空的規則不在範圍內：那是本功能導入前建立的舊規則，
    它們本來就沒有藥品清單，推出去的版面與過去一致，不是這次要清的東西。
    """
    reminders = {
        doc["_id"]: doc
        for doc in db["medication_reminders"].find(
            {**user_filter, "medication_ids": {"$exists": True, "$ne": []}}
        )
    }
    if not reminders:
        return []

    medication_ids = sorted(
        {mid for doc in reminders.values() for mid in doc.get("medication_ids", [])}
    )
    medications = {
        doc["_id"]: doc
        for doc in db["medications"].find({"_id": {"$in": medication_ids}})
    }

    logs = db["medication_logs"].find(
        {
            **user_filter,
            "reminder_id": {"$in": list(reminders)},
            # taken 不動：使用者按過確認就是按過。
            "status": {"$in": ["pending", "missed"]},
            "scheduled_at": {"$gte": since},
        }
    ).sort("scheduled_at", 1)

    affected = []
    for log in logs:
        reminder = reminders[log["reminder_id"]]
        date_str = _taipei_date_str(log["scheduled_at"])
        has_active = any(
            _is_active_on(medications[mid], date_str)
            for mid in reminder.get("medication_ids", [])
            if mid in medications
        )
        if not has_active:
            affected.append(log)
    return affected


def main() -> None:
    parser = argparse.ArgumentParser(
        description="清掉療程結束後仍展開的空提醒紀錄（預設只讀不寫）"
    )
    parser.add_argument("--user-id", help="只檢查這位使用者")
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS, help=f"往回追幾天（預設 {DEFAULT_DAYS}）"
    )
    parser.add_argument(
        "--apply", action="store_true", help="實際寫入：把這些紀錄改成 cancelled"
    )
    args = parser.parse_args()

    load_dotenv()
    client = pymongo.MongoClient(_get_mongodb_url())
    db = client[os.getenv("MONGODB_DB", DEFAULT_DB_NAME)]

    user_filter = {"user_id": args.user_id} if args.user_id else {}
    # 資料庫裡的時間是 naive UTC，比較對象也要拿掉時區。
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).replace(tzinfo=None)

    print(f"資料庫：{db.name}")
    print(
        f"範圍　：{'user=' + args.user_id if args.user_id else '全部使用者'}"
        f"，最近 {args.days} 天（{_fmt(since)} 起）"
    )
    print(f"模式　：{'寫入（--apply）' if args.apply else '只讀（預設）'}")

    affected = find_empty_card_logs(db, user_filter, since)
    if not affected:
        print("\n   ✅ 沒有找到療程結束後展開的空紀錄。")
        client.close()
        return

    by_status = Counter(log["status"] for log in affected)
    by_user = Counter(log["user_id"] for log in affected)

    print(f"\n{'─' * 72}\n找到 {len(affected)} 筆空提醒紀錄\n{'─' * 72}")
    for log in affected:
        print(
            f"   {_fmt(log['scheduled_at'])}  {log['slot_type']:<8}"
            f"  status={log['status']:<8}  user={log['user_id'][:12]}…"
            f"  reminder={log['reminder_id']}"
        )

    print(f"\n{'─' * 72}\n分佈\n{'─' * 72}")
    for status, count in by_status.most_common():
        print(f"   status={status}：{count} 筆")
    for user_id, count in by_user.most_common():
        print(f"   user={user_id}：{count} 筆")
    if by_status.get("missed"):
        print(
            f"\n   ⚠️  其中 {by_status['missed']} 筆是 missed——這些時段當初確實送出過"
            "\n       家屬逾時警報。改成 cancelled 會讓資料庫與家屬當時收到的通知"
            "\n       不再一致；決定 --apply 之前請先確認這是可接受的。"
        )

    if not args.apply:
        print("\n   本次只讀不寫。確認清單無誤後，加上 --apply 實際寫入。")
        client.close()
        return

    result = db["medication_logs"].update_many(
        {"_id": {"$in": [log["_id"] for log in affected]}},
        {"$set": {"status": "cancelled"}},
    )
    print(f"\n   ✅ 已將 {result.modified_count} 筆改為 cancelled（matched={result.matched_count}）")
    client.close()


if __name__ == "__main__":
    main()
