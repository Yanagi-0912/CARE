"""用藥提醒／日誌的資料健檢——找出會造成「已確認卻仍收到家人關心提醒」的成因。

這支腳本**只讀不寫**，刻意不提供任何 --fix：重複的規則要留哪一份、被蓋掉的
用藥紀錄要不要改回 taken，都牽涉到「使用者當時到底吃了沒」這種腳本無從判斷
的事實，必須由人看過每一筆再決定。

六項檢查，前兩項是成因，後四項是後果（有沒有真的發生過）：

  1. 同一位使用者、同一時段有多份提醒規則
     `find_or_create_reminder` 的查詢條件只看 {user_id, slot_type}，理論上一個
     時段只會有一份 document——但那個保證是後來才收斂的，而且 (user_id,
     slot_type) 上刻意沒有唯一索引（舊資料若已重複，建索引會讓應用起不來）。
     一個時段兩份規則 = 每天兩筆 log = 兩張長得一模一樣的提醒卡；使用者按掉
     其中一張，另一筆仍是 pending，30 分鐘後照樣通報家屬。

  2. medication_logs 的唯一索引是否真的建起來
     `ensure_indexes` 建索引失敗時只記 log、不讓 app 起不來（見該方法的註解），
     所以「索引不存在」是一個會安靜存在很久的狀態。少了它，多實例並存時同一個
     時段會有兩筆 log，推播權搶佔形同虛設。

  3. 同一個 (reminder_id, scheduled_at) 有多筆 log
     檢查 2 的實際後果。有這種資料就代表唯一索引當初沒建成功。

  4. 同一位使用者、同一個時間點有多筆 log（跨 reminder_id）
     檢查 1 的實際後果，也是使用者最直接感受到的那個症狀。

  5. status=missed 但有 taken_at —— 用藥紀錄被推播流程蓋掉
     這是 claim_caregiver_alert 少了 `status: "pending"` 條件的鐵證：使用者
     確實按過確認（所以有 taken_at），紀錄卻是 missed。修正後不會再新增，
     但既有的髒資料仍在。

  6. 確認時間早於逾時期限、家屬卻仍收到警報
     使用者在 30 分鐘內就按了確認，caregiver_alert_sent 卻是 True。這就是
     這次回報的症狀本身；每一筆都附上時間差，用來分辨成因：
       * 差距只有幾秒 → 搶佔競態（已修）
       * 差距是分鐘等級 → 多半是按到別筆 log（重複規則／舊卡片）

用法：
    .venv/bin/python scripts/check_medication_duplicates.py
    .venv/bin/python scripts/check_medication_duplicates.py --user-id U123...
    .venv/bin/python scripts/check_medication_duplicates.py --days 30
"""

import argparse
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pymongo
from dotenv import load_dotenv

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
DEFAULT_DB_NAME = "CARE_database"
DEFAULT_DAYS = 14


def _get_mongodb_url() -> str:
    url = os.getenv("MONGODB_URL") or os.getenv("MONGODB_URI")
    if not url:
        raise RuntimeError("Missing MONGODB_URL or MONGODB_URI")
    return url


def _fmt(dt: Optional[datetime]) -> str:
    """把資料庫取回的時間印成台北時間。

    Motor/pymongo 未啟用 tz_aware，時間是以 naive UTC 讀回的；直接 strftime
    會印出比實際早 8 小時的時刻，剛好足以讓人把「差 3 秒」誤讀成「差 8 小時」。
    """
    if dt is None:
        return "—"
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _section(index: int, title: str) -> None:
    print(f"\n{'─' * 72}\n{index}. {title}\n{'─' * 72}")


def _ok(message: str) -> None:
    print(f"   ✅ {message}")


def _warn(message: str) -> None:
    print(f"   ⚠️  {message}")


def check_duplicate_reminders(db, user_filter: dict[str, Any]) -> int:
    _section(1, "同一位使用者、同一時段的重複提醒規則")
    pipeline = [
        *([{"$match": user_filter}] if user_filter else []),
        {
            "$group": {
                "_id": {"user_id": "$user_id", "slot_type": "$slot_type"},
                "count": {"$sum": 1},
                "reminders": {
                    "$push": {
                        "id": "$_id",
                        "scheduled_time": "$scheduled_time",
                        "enabled": "$enabled",
                        "start_date": "$start_date",
                        "end_date": "$end_date",
                        "creator_user_id": "$creator_user_id",
                        "medication_ids": "$medication_ids",
                        "created_at": "$created_at",
                    }
                },
            }
        },
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"count": -1}},
    ]
    groups = list(db["medication_reminders"].aggregate(pipeline))
    if not groups:
        _ok("沒有重複的提醒規則")
        return 0

    for group in groups:
        key = group["_id"]
        _warn(
            f"user={key['user_id']} slot={key['slot_type']} 有 {group['count']} 份規則："
        )
        for reminder in group["reminders"]:
            enabled = reminder.get("enabled")
            # 只有「同時啟用」的重複規則才會造成雙重推播；停用的那份留著不影響
            # 使用者，但仍是重複，往後被 find_or_create_reminder 復活就會出事。
            state = "啟用中" if enabled else f"已停用(enabled={enabled!r})"
            meds = reminder.get("medication_ids") or []
            print(
                f"        - {reminder['id']}  {reminder.get('scheduled_time')}  {state}  "
                f"藥品 {len(meds)} 種  {reminder.get('start_date')}~{reminder.get('end_date')}  "
                f"建立於 {_fmt(reminder.get('created_at'))}"
            )
        live = [r for r in group["reminders"] if r.get("enabled")]
        if len(live) > 1:
            _warn(
                f"      ↑ 其中 {len(live)} 份同時啟用 —— 這個時段每天會產生 "
                f"{len(live)} 筆 log、推播 {len(live)} 張卡片"
            )
    return len(groups)


def check_log_unique_index(db) -> int:
    _section(2, "medication_logs 的 (reminder_id, scheduled_at) 唯一索引")
    indexes = db["medication_logs"].index_information()
    for name, spec in indexes.items():
        keys = [k for k, _ in spec.get("key", [])]
        if keys == ["reminder_id", "scheduled_at"]:
            if spec.get("unique"):
                _ok(f"唯一索引存在（{name}）")
                return 0
            _warn(f"索引 {name} 存在但不是 unique —— 擋不住併發重複插入")
            return 1
    _warn(
        "唯一索引不存在。ensure_indexes 建立失敗時只記 log 不中斷啟動，"
        "所以這個狀態會安靜地持續；多實例並存時同一時段會有兩筆 log。"
    )
    return 1


def check_duplicate_logs(db, user_filter: dict[str, Any], since: datetime) -> int:
    _section(3, "同一個 (reminder_id, scheduled_at) 的重複 log")
    match: dict[str, Any] = {"scheduled_at": {"$gte": since}}
    match.update(user_filter)
    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": {"reminder_id": "$reminder_id", "scheduled_at": "$scheduled_at"},
                "count": {"$sum": 1},
                "logs": {"$push": {"id": "$_id", "status": "$status"}},
            }
        },
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"_id.scheduled_at": -1}},
    ]
    groups = list(db["medication_logs"].aggregate(pipeline))
    if not groups:
        _ok("沒有重複的 log")
        return 0

    for group in groups:
        key = group["_id"]
        _warn(
            f"reminder={key['reminder_id']} {_fmt(key['scheduled_at'])} "
            f"有 {group['count']} 筆 log："
            + ", ".join(f"{log['id']}({log['status']})" for log in group["logs"])
        )
    return len(groups)


def check_same_slot_multiple_logs(db, user_filter: dict[str, Any], since: datetime) -> int:
    _section(4, "同一位使用者、同一個時間點的多筆 log（跨 reminder）")
    match: dict[str, Any] = {"scheduled_at": {"$gte": since}}
    match.update(user_filter)
    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": {"user_id": "$user_id", "scheduled_at": "$scheduled_at"},
                "count": {"$sum": 1},
                "logs": {
                    "$push": {
                        "id": "$_id",
                        "reminder_id": "$reminder_id",
                        "status": "$status",
                        "taken_at": "$taken_at",
                        "caregiver_alert_sent": "$caregiver_alert_sent",
                    }
                },
            }
        },
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"_id.scheduled_at": -1}},
    ]
    groups = list(db["medication_logs"].aggregate(pipeline))
    if not groups:
        _ok("沒有同時段的多筆 log")
        return 0

    for group in groups:
        key = group["_id"]
        _warn(
            f"user={key['user_id']} {_fmt(key['scheduled_at'])} 有 {group['count']} 筆 log："
        )
        for log in group["logs"]:
            alert = "已通報家屬" if log.get("caregiver_alert_sent") else "未通報"
            print(
                f"        - {log['id']}  reminder={log['reminder_id']}  "
                f"status={log['status']}  taken_at={_fmt(log.get('taken_at'))}  {alert}"
            )
        statuses = {log["status"] for log in group["logs"]}
        if "taken" in statuses and statuses - {"taken"}:
            _warn(
                "      ↑ 同一個時段有的已確認、有的沒有 —— 使用者按掉其中一張卡，"
                "另一筆仍會照常通報家屬"
            )
    return len(groups)


def check_clobbered_taken_logs(db, user_filter: dict[str, Any], since: datetime) -> int:
    _section(5, "status=missed 但有 taken_at（用藥紀錄被推播流程蓋掉）")
    query: dict[str, Any] = {
        "status": "missed",
        "taken_at": {"$ne": None},
        "scheduled_at": {"$gte": since},
    }
    query.update(user_filter)
    logs = list(db["medication_logs"].find(query).sort("scheduled_at", -1))
    if not logs:
        _ok("沒有被蓋掉的用藥紀錄")
        return 0

    _warn(
        f"{len(logs)} 筆。這是 claim_caregiver_alert 少了 status 條件的直接後果："
        "使用者確實按過確認，紀錄卻被改回 missed。"
    )
    for log in logs:
        print(
            f"        - {log['_id']}  user={log.get('user_id')}  "
            f"排程 {_fmt(log.get('scheduled_at'))}  確認 {_fmt(log.get('taken_at'))}"
        )
    return len(logs)


def check_alert_after_confirmation(db, user_filter: dict[str, Any], since: datetime) -> int:
    _section(6, "確認早於逾時期限、家屬卻仍收到警報")
    query: dict[str, Any] = {
        "caregiver_alert_sent": True,
        "taken_at": {"$ne": None},
        "scheduled_at": {"$gte": since},
    }
    query.update(user_filter)
    logs = list(db["medication_logs"].find(query).sort("scheduled_at", -1))

    offenders = []
    for log in logs:
        taken_at = log.get("taken_at")
        timeout_at = log.get("timeout_at")
        if not taken_at or not timeout_at:
            continue
        # taken_at <= timeout_at 代表使用者在 30 分鐘期限內就確認了，
        # 家屬警報本來就不該送出。晚於期限才確認的不算——那則警報是對的。
        if taken_at <= timeout_at:
            offenders.append((log, (timeout_at - taken_at).total_seconds()))

    if not offenders:
        if logs:
            _ok(
                f"{len(logs)} 筆在通報後才確認（警報屬實），沒有「期限內確認卻仍通報」的紀錄"
            )
        else:
            _ok("沒有這種紀錄")
        return 0

    _warn(f"{len(offenders)} 筆：使用者在期限內就確認了，家屬仍收到警報。")
    for log, slack in offenders:
        # 提前量越小越像競態；分鐘等級的提前量代表使用者早就按了，
        # 那則警報多半來自「另一筆」log（重複規則或按到舊卡片）。
        hint = "疑似搶佔競態" if slack < 60 else "疑似按到別筆 log（見檢查 1、4）"
        print(
            f"        - {log['_id']}  user={log.get('user_id')}  "
            f"排程 {_fmt(log.get('scheduled_at'))}  確認 {_fmt(log.get('taken_at'))}  "
            f"距期限還有 {slack:.0f} 秒  → {hint}"
        )
    return len(offenders)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="用藥提醒／日誌資料健檢（唯讀，不修改任何資料）"
    )
    parser.add_argument("--user-id", help="只檢查特定使用者的 LINE userId")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"log 相關的檢查只看最近幾天（預設 {DEFAULT_DAYS}）。規則重複檢查不受此限。",
    )
    parser.add_argument("--db", default=os.getenv("MONGODB_DB") or DEFAULT_DB_NAME)
    args = parser.parse_args()

    load_dotenv()
    client = pymongo.MongoClient(_get_mongodb_url())
    db = client[args.db]

    user_filter = {"user_id": args.user_id} if args.user_id else {}
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    # 資料庫裡的時間是 naive UTC（pymongo 未啟用 tz_aware），比較對象也要拿掉
    # 時區，否則 pymongo 會把 aware datetime 正確轉換、但本地端的比較會炸。
    since_naive = since.replace(tzinfo=None)

    print(f"資料庫：{args.db}")
    print(f"範圍　：{'user=' + args.user_id if args.user_id else '全部使用者'}"
          f"，log 取最近 {args.days} 天（{_fmt(since_naive)} 起）")

    counts = {
        "重複的提醒規則": check_duplicate_reminders(db, user_filter),
        "唯一索引問題": check_log_unique_index(db),
        "重複的 log": check_duplicate_logs(db, user_filter, since_naive),
        "同時段多筆 log": check_same_slot_multiple_logs(db, user_filter, since_naive),
        "被蓋掉的用藥紀錄": check_clobbered_taken_logs(db, user_filter, since_naive),
        "期限內確認仍通報": check_alert_after_confirmation(db, user_filter, since_naive),
    }

    print(f"\n{'═' * 72}\n總結\n{'═' * 72}")
    for label, count in counts.items():
        print(f"   {'⚠️ ' if count else '✅'} {label}：{count}")
    if not any(counts.values()):
        print("\n   資料面沒有發現問題。")
    else:
        print("\n   本腳本不修改任何資料；每一筆都需要人工確認後再決定怎麼處理。")

    client.close()


if __name__ == "__main__":
    main()
