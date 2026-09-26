#!/usr/bin/env python3
r"""醫療院所名稱查詢覆蓋率：資料庫裡每一家院所，用它自己的名稱查得不查得到。

量的是什麼：
    對 medicalFacilities 的每一家院所，用它登記的名稱呼叫正式的
    MedicalService.find_facility_by_name，看結果（最多 20 筆）裡有沒有它自己：
    - 不帶座標：同名的院所查的是同一句，所以每個不重複的名稱只查一次，結果套用到同名的每一家；
    - 帶座標：用該院所自己的座標查，模擬「使用者就在附近」，每家各查一次。
    覆蓋率＝查得到的家數 ÷ 資料庫總家數，另列「排第一」的比例，並依院所類型分開列。

    用完整登記名稱查是最好查的情況，數字是**上限**；使用者平常講簡稱（臺大醫院、長庚），
    那要另外準備案例。

分母：
    只算 medicalFacilities。藥局在另一個 collection（medical_facilities_pharmacy），名稱查詢
    目前不查那裡，算進分母只會反映「功能還沒做」；報告另列藥局家數備註。

資料庫：
    **只讀** MONGODB_DB（正式環境是 CARE_database），不寫入。每次查詢都是名稱 regex，會掃過整個
    collection。全部跑約 19,400（不帶座標）＋ 23,200（帶座標）≈ 42,600 次查詢；--concurrency 2
    （預設）時預估約 15～20 分鐘。建議先用 --limit 200 試跑，再挑離峰時段跑完整版。

不呼叫 Gemini：名稱查詢在正式程式裡本來就只查資料庫。

用法（專案根目錄，需要 .env 裡的 MONGODB_URI、MONGODB_DB）：
  PowerShell：.\.venv\Scripts\python.exe scripts\medical_bench\facility_name_coverage.py --env-label local --limit 200
  Git Bash  ：.venv/Scripts/python.exe scripts/medical_bench/facility_name_coverage.py --env-label local --limit 200
  GCP 暫時 pod：本腳本與 facility_search_bench.py 都複製到 /tmp，cd /app && python
                /tmp/facility_name_coverage.py --env-label gcp-pod --out /tmp/facility-coverage-reports

輸出：<out>/facility-name-coverage-<env-label>-YYYYMMDD-HHMMSS.json（每一家的結果）與同名 .md（摘要）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import random
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# 同一個資料夾的院所查詢量測：共用專案根目錄的找法、.env 載入與 DB 錯誤收集。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from facility_search_bench import (  # noqa: E402
    PROJECT_ROOT,
    TAIPEI,
    install_db_error_collector,
    is_timeout,
    timed_call,
)

from app.core.config import settings  # noqa: E402
from app.db.mongodb import MongoDBManager  # noqa: E402
from app.services.medical.medical_service import (  # noqa: E402
    NAME_SEARCH_RADIUS_METERS,
    MedicalService,
)

PHARMACY_COLLECTION = "medical_facilities_pharmacy"
PROGRESS_EVERY = 1000


async def load_facilities() -> list[dict]:
    """唯讀取出每一家的 id、名稱、類型與座標（location 是 GeoJSON [lng, lat]）。"""
    collection = MongoDBManager.get_medical_collection()
    facilities = []
    async for doc in collection.find({}, {"name": 1, "type": 1, "location": 1}):
        lng, lat = doc["location"]["coordinates"]
        facilities.append(
            {
                "id": str(doc["_id"]),
                "name": doc.get("name") or "",
                "type": doc.get("type") or "（未填）",
                "lat": lat,
                "lng": lng,
            }
        )
    return facilities


async def count_pharmacies() -> int:
    return await MongoDBManager.get_database()[PHARMACY_COLLECTION].estimated_document_count()


async def query_all(calls: list, concurrency: int, interval: float, title: str) -> list[dict]:
    """用固定數量的 worker 依序消化 calls，回傳與 calls 同順序的結果。

    不一次 gather 全部：四萬多個 task 同時存在，在 256Mi 的暫時 pod 裡太吃記憶體。
    """
    answers: list[dict | None] = [None] * len(calls)
    next_index = 0
    done = 0
    started = time.perf_counter()

    async def worker() -> None:
        nonlocal next_index, done
        while next_index < len(calls):
            index = next_index
            next_index += 1
            seconds, result, errors = await timed_call(calls[index])
            facilities, _count = result
            answers[index] = {
                "seconds": round(seconds, 3),
                "ids": [f.id for f in facilities],
                "errors": errors,
            }
            done += 1
            if done % PROGRESS_EVERY == 0:
                elapsed = time.perf_counter() - started
                print(f"  [{title}] {done}/{len(calls)}，已 {elapsed / 60:.1f} 分鐘", flush=True)
            if interval:
                await asyncio.sleep(interval)

    await asyncio.gather(*(worker() for _ in range(max(1, concurrency))))
    print(f"  [{title}] 完成 {len(calls)} 次，{(time.perf_counter() - started) / 60:.1f} 分鐘")
    return answers


def _outcome(target_id: str, answer: dict) -> dict:
    ids = answer["ids"]
    rank = ids.index(target_id) + 1 if target_id in ids else None
    return {
        "found": rank is not None,
        "rank": rank,
        "results": len(ids),
        "failed": bool(answer["errors"]),
        "timeout": any(is_timeout(e) for e in answer["errors"]),
    }


async def run(
    facilities: list[dict], modes: list[str], concurrency: int, interval: float
) -> dict[str, list[dict]]:
    service = MedicalService()
    outcomes: dict[str, list[dict]] = {}

    if "no-location" in modes:
        names = sorted({f["name"] for f in facilities})
        answers = await query_all(
            [lambda n=name: service.find_facility_by_name(n) for name in names],
            concurrency, interval, "不帶座標",
        )
        by_name = dict(zip(names, answers))
        outcomes["no-location"] = [
            {"id": f["id"], **_outcome(f["id"], by_name[f["name"]])} for f in facilities
        ]

    if "with-location" in modes:
        answers = await query_all(
            [
                lambda f=f: service.find_facility_by_name(f["name"], lat=f["lat"], lng=f["lng"])
                for f in facilities
            ],
            concurrency, interval, "帶座標",
        )
        outcomes["with-location"] = [
            {"id": f["id"], **_outcome(f["id"], answer)} for f, answer in zip(facilities, answers)
        ]
    return outcomes


def _ratio(part: int, whole: int) -> str:
    return f"{part:,}（{part / whole:.1%}）" if whole else "—"


def _summarize(facilities: list[dict], results: list[dict]) -> dict:
    types = {f["id"]: f["type"] for f in facilities}
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        groups[types[r["id"]]].append(r)

    def block(rows: list[dict]) -> dict:
        return {
            "total": len(rows),
            "found": sum(r["found"] for r in rows),
            "top1": sum(r["rank"] == 1 for r in rows),
            "failures": sum(r["failed"] for r in rows),
            "timeouts": sum(r["timeout"] for r in rows),
        }

    return {
        "overall": block(results),
        "by_type": {
            t: block(rows) for t, rows in sorted(groups.items(), key=lambda kv: -len(kv[1]))
        },
    }


MODE_TITLES = {"no-location": "不帶座標", "with-location": "帶該院所座標"}


def _markdown(meta: dict, summaries: dict, misses: dict[str, list[dict]]) -> str:
    lines = [
        f"# 醫療院所名稱查詢覆蓋率 {meta['started_at']}",
        "",
        f"- 執行環境：**{meta['env_label']}**（{meta['platform']}，Python {meta['python']}）",
        f"- 資料庫：`{meta['database']}` 的 `medicalFacilities`，唯讀；"
        f"測了 {meta['tested']:,} 家（資料庫共 {meta['db_total']:,} 家"
        + ("，隨機抽樣" if meta["tested"] < meta["db_total"] else "")
        + "）",
        "- 做法：用每家院所自己的登記名稱呼叫 `MedicalService.find_facility_by_name`，"
        "看最多 20 筆的結果裡有沒有它自己。",
        "  - 不帶座標：同名院所查同一句，結果套用到同名的每一家。",
        f"  - 帶座標：用該院所自己的座標查，先查 {NAME_SEARCH_RADIUS_METERS // 1000} 公里內，"
        "查無再放寬全國（與正式程式相同）。",
        "- 用完整登記名稱查是最好查的情況，覆蓋率是**上限**；使用者講簡稱的情況不在這份報告裡。",
        f"- 分母不含藥局：藥局在 `{PHARMACY_COLLECTION}`（{meta['pharmacy_total']:,} 家），"
        "名稱查詢目前不查那裡。",
        "- 失敗＝repository 記下 DB 錯誤（正式程式此時回空清單，會被算成查不到）。",
    ]
    for mode, summary in summaries.items():
        o = summary["overall"]
        lines += [
            "",
            f"## {MODE_TITLES[mode]}",
            "",
            f"查得到 {_ratio(o['found'], o['total'])}，排第一 {_ratio(o['top1'], o['total'])}，"
            f"失敗 {o['failures']}、逾時 {o['timeouts']}。",
            "",
            "| 院所類型 | 家數 | 查得到 | 排第一 | 失敗 | 逾時 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for t, b in summary["by_type"].items():
            lines.append(
                f"| {t} | {b['total']:,} | {_ratio(b['found'], b['total'])} "
                f"| {_ratio(b['top1'], b['total'])} | {b['failures']} | {b['timeouts']} |"
            )
        examples = misses[mode][:20]
        if examples:
            lines += ["", f"查不到的例子（前 {len(examples)} 家，完整清單在 json）：", ""]
            lines += [f"- {m['name']}（{m['type']}）：結果 {m['results']} 筆" for m in examples]
    return "\n".join(lines) + "\n"


async def _main_async(
    args: argparse.Namespace,
) -> tuple[list[dict], dict[str, list[dict]], int, int]:
    MongoDBManager.configure(settings.MONGODB_URI)
    install_db_error_collector()
    facilities = await load_facilities()
    db_total = len(facilities)
    if args.limit and args.limit < db_total:
        facilities = random.Random(args.seed).sample(facilities, args.limit)
    pharmacy_total = await count_pharmacies()

    names = len({f["name"] for f in facilities})
    planned = (names if "no-location" in args.modes else 0) + (
        len(facilities) if "with-location" in args.modes else 0
    )
    print(
        f"資料庫 {db_total:,} 家，這次測 {len(facilities):,} 家（不重複名稱 {names:,} 個），"
        f"預計 {planned:,} 次唯讀查詢，同時 {args.concurrency} 個"
    )
    outcomes = await run(facilities, args.modes, args.concurrency, args.interval)
    return facilities, outcomes, pharmacy_total, db_total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--env-label", required=True, help="執行環境，寫進報告與摘要，例如 local、gcp-pod"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="隨機抽幾家來測，0＝全部（預設 0；試跑建議 200）"
    )
    parser.add_argument("--seed", type=int, default=20260926, help="抽樣的亂數種子")
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=list(MODE_TITLES),
        default=list(MODE_TITLES),
        help="要跑哪幾種（預設兩種都跑）",
    )
    parser.add_argument(
        "--concurrency", type=int, default=2, help="同時送出幾個查詢（預設 2）"
    )
    parser.add_argument(
        "--interval", type=float, default=0.0, help="每個查詢完成後再等幾秒（預設 0）"
    )
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "evals" / "facility_search" / "reports"),
        help="報告輸出資料夾（預設 evals/facility_search/reports）",
    )
    args = parser.parse_args()

    started = datetime.now(TAIPEI)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    wall = time.perf_counter()
    facilities, outcomes, pharmacy_total, db_total = asyncio.run(_main_async(args))
    wall = time.perf_counter() - wall

    by_id = {f["id"]: f for f in facilities}
    summaries = {mode: _summarize(facilities, rows) for mode, rows in outcomes.items()}
    misses = {
        mode: [{**by_id[r["id"]], **r} for r in rows if not r["found"]]
        for mode, rows in outcomes.items()
    }

    meta = {
        "started_at": started.isoformat(timespec="seconds"),
        "env_label": args.env_label,
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
        "database": settings.MONGODB_DB,
        "db_total": db_total,
        "tested": len(facilities),
        "seed": args.seed if args.limit else None,
        "pharmacy_total": pharmacy_total,
        "modes": args.modes,
        "concurrency": args.concurrency,
        "interval": args.interval,
        "wall_minutes": round(wall / 60, 1),
    }
    # 查不到的清單可由 facilities 與 results 對出來，不另存。
    report = {"meta": meta, "summary": summaries, "facilities": facilities, "results": outcomes}

    json_path = out / f"facility-name-coverage-{args.env_label}-{stamp}.json"
    md_path = out / f"facility-name-coverage-{args.env_label}-{stamp}.md"
    # 全部跑時有兩萬多家×兩種結果，不縮排，檔案約小一半。
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    md_path.write_text(_markdown(meta, summaries, misses), encoding="utf-8")
    print(f"\n{_markdown(meta, summaries, misses)}")
    print(f"總耗時 {meta['wall_minutes']} 分鐘\n報告：{json_path}\n摘要：{md_path}")


if __name__ == "__main__":
    main()
