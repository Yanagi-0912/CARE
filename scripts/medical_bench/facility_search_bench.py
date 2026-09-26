#!/usr/bin/env python3
r"""醫療院所查詢回應時間量測：正式程式的 MedicalService，32 個案例各量數次。

量的是什麼：
    LINE 上「找附近院所」三個工具背後的 service 呼叫，從呼叫到拿回院所清單的時間：
    - 不分科：MedicalService.find_nearby_hospitals（含「現在有營業」與院所類型）
    - 依科別：MedicalService.find_nearby_facilities_by_department（含多科、科別＋類型）
    - 依名稱：MedicalService.find_facility_by_name（有帶座標與沒帶座標）
    幾乎全是 MongoDB 查詢（$geoNear、名稱 regex），數字反映「這台機器到正式資料庫」加上
    資料庫的查詢時間。要代表正式環境，就在 GCP VM 上用 backend 的映像開暫時 pod 跑（步驟見
    evals/facility_search/README.md）。

不含：
    Gemini agent 判斷要叫哪個工具的時間（使用者等待的大頭）、Flex 卡片產生、LINE 推播。

不呼叫 Gemini：
    正式程式在科別或院所類型「本地表查不到」時才交給 LLM 兜底。本腳本建立的 MedicalService
    不接兜底解析器，並在開跑前確認每個案例的科別與類型都能被本地表解析；有任何一個解析不出來
    就直接結束，不會退化成呼叫 LLM。

資料庫：
    **只讀** MONGODB_DB（正式環境是 CARE_database）的 medicalFacilities，不寫入。預設 32 個案例
    ×（1 次暖身＋5 次）＝ 192 次 service 呼叫，一次只送一個。

「現在有營業」的結果隨執行時刻而變（午休、深夜篩掉的家數不同），報告記錄執行時間。

用法（專案根目錄，需要 .env 裡的 MONGODB_URI、MONGODB_DB）：
  PowerShell：.\.venv\Scripts\python.exe scripts\medical_bench\facility_search_bench.py --env-label local
  Git Bash  ：.venv/Scripts/python.exe scripts/medical_bench/facility_search_bench.py --env-label local
  GCP 暫時 pod：cd /app && python /tmp/facility_search_bench.py --env-label gcp-pod --out /tmp/facility-search-reports

輸出：<out>/facility-search-<env-label>-YYYYMMDD-HHMMSS.json（每一次的原始數據）與同名 .md（摘要）。
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import logging
import platform
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _project_root() -> Path:
    """找含有 CARE 程式（app/services）的目錄：先看工作目錄，再從本檔往上逐層找。

    在 repo 裡執行時是 repo 根目錄；被 kubectl cp 到 pod 的 /tmp、在 /app 底下執行時
    是 /app。不能固定往上取第幾層：/tmp/facility_search_bench.py 往上只有兩層。
    """
    here = Path(__file__).resolve()
    for candidate in (Path.cwd(), *here.parents):
        if (candidate / "app" / "services").is_dir():
            return candidate
    raise SystemExit(
        "找不到 CARE 的 app 套件：請在專案根目錄（pod 裡是 /app）底下執行。"
    )


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 本機從 .env 讀 MONGODB_URI／MONGODB_DB；pod 裡由環境變數提供。
# 一定要在 import 任何 app 模組之前載入：設定是在 import 當下從環境變數讀的。
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:  # pragma: no cover - 正式映像有裝 python-dotenv
    pass

from app.core.config import settings  # noqa: E402
from app.db.mongodb import MongoDBManager  # noqa: E402
from app.services.medical.department_matcher import resolve_department  # noqa: E402
from app.services.medical.facility_type_matcher import (  # noqa: E402
    resolve_facility_type,
)
from app.services.medical.medical_service import (  # noqa: E402
    NAME_SEARCH_RADIUS_METERS,
    MedicalService,
)

TAIPEI = timezone(timedelta(hours=8))

# 三個地點依院所密度挑（2026-09-26 唯讀查 medicalFacilities：5／10／20／50 公里內的家數）：
#   台北車站 2,680／5,978／7,003／9,395：5 公里內就湊滿
#   埔里       85／93／106／4,536：一般科別 5 公里湊滿，少見科別要放寬
#   蘭嶼        1／1／1／1：一路放寬到 50 公里仍湊不滿
LOCATIONS: dict[str, tuple[float, float]] = {
    "台北車站": (25.0478, 121.5170),
    "埔里": (23.9651, 120.9672),
    "蘭嶼": (22.0447, 121.5486),
    # 只給「依名稱、生活圈內查無 → 放寬全國」那一案用：高雄查臺大醫院。
    "高雄車站": (22.6394, 120.3025),
}

# 使用者指定的極端地點（2026-09-26），各查「不分科」與「不分科」科別。
# 同日唯讀查 5／10／20／50 公里內的家數（全部／不分科）：
#   海洋大學       209／318／618／7,812；23／43／85／785
#   金門            42／59／62／62；7／10／11／11
#   澎湖望安         2／2／3／87；0／0／0／6
#   壽卡鐵馬驛站     0／1／8／274；0／0／5／74（屏東台東交界）
#   彭佳嶼           0／0／0／0；0／0／0／0（50 公里內查無，預期回「查無院所」）
#   豐濱鄉衛生所     2／2／23／272；1／1／9／52
#   梨山             1／1／1／427；0／0／0／75
EDGE_LOCATIONS: dict[str, tuple[float, float]] = {
    "海洋大學": (25.1505781267729, 121.77593993971523),
    "金門": (24.42901055177892, 118.35925343191302),
    "澎湖望安": (23.365146141791495, 119.50227722091145),
    "壽卡鐵馬驛站": (22.245351816663483, 120.83557557682634),
    "彭佳嶼": (25.628849019985626, 122.07952159367875),
    "豐濱鄉衛生所": (23.601314196172822, 121.52138277796175),
    "梨山": (24.254865345062033, 121.2516393019278),
}
LOCATIONS.update(EDGE_LOCATIONS)


@dataclass(frozen=True)
class Case:
    key: str
    kind: str  # nearby／department／name
    label: str
    location: str | None = None
    departments: tuple[str, ...] = ()
    facility_type: str | None = None
    open_now: bool = False
    keyword: str | None = None


CASES: list[Case] = [
    Case("nearby-taipei", "nearby", "不分科", "台北車站"),
    Case("nearby-puli", "nearby", "不分科", "埔里"),
    Case("nearby-lanyu", "nearby", "不分科", "蘭嶼"),
    Case("open-taipei", "nearby", "不分科＋現在有營業", "台北車站", open_now=True),
    Case("open-puli", "nearby", "不分科＋現在有營業", "埔里", open_now=True),
    Case("open-lanyu", "nearby", "不分科＋現在有營業", "蘭嶼", open_now=True),
    Case("type-clinic-puli", "nearby", "類型：診所", "埔里", facility_type="診所"),
    Case("dept-gi-taipei", "department", "腸胃科", "台北車站", departments=("腸胃科",)),
    Case("dept-dental-puli", "department", "牙科", "埔里", departments=("牙科",)),
    Case(
        "dept-rare-puli", "department", "放射腫瘤科（少見）", "埔里",
        departments=("放射腫瘤科",),
    ),
    Case(
        "dept-fallback-taipei", "department", "家醫科＋內科＋不分科（保底卡按鈕）", "台北車站",
        departments=("家醫科", "內科", "不分科"),
    ),
    Case(
        "dept-type-taipei", "department", "大醫院的腸胃科", "台北車站",
        departments=("腸胃科",), facility_type="大醫院",
    ),
    Case("name-ntuh", "name", "臺大醫院（別名，不帶座標）", keyword="臺大醫院"),
    Case(
        "name-ntuh-kaohsiung", "name", "臺大醫院（高雄查，生活圈內查無→全國）", "高雄車站",
        keyword="臺大醫院",
    ),
    Case("name-renai", "name", "仁愛診所（同名多家，不帶座標）", keyword="仁愛診所"),
    Case("name-renai-taipei", "name", "仁愛診所（同名多家，帶座標）", "台北車站", keyword="仁愛診所"),
    Case("name-city-prefix", "name", "花蓮中正診所（開頭是地名）", keyword="花蓮中正診所"),
    Case("name-missing", "name", "不存在的院所（帶座標，會查兩次）", "台北車站", keyword="王小明診所"),
]
# 極端地點：找附近院所；要找科別時一律找不分科。
for _place in EDGE_LOCATIONS:
    CASES += [
        Case(f"edge-nearby-{_place}", "nearby", "不分科", _place),
        Case(f"edge-dept-{_place}", "department", "科別：不分科", _place, departments=("不分科",)),
    ]


# 正式 repository 查詢出錯時只記 ERROR log、回傳空清單，呼叫端分不出「查無」和「DB 出錯」。
# 用 ContextVar 把 log 歸到發出它的那一次呼叫（併發時也歸得準），才能算失敗與逾時次數。
_errors: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "bench_db_errors", default=None
)


class DbErrorCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        bucket = _errors.get()
        if bucket is not None:
            bucket.append(record.getMessage()[:200])


def install_db_error_collector() -> None:
    logger = logging.getLogger("app.repositories.medical_facility_repository")
    logger.addHandler(DbErrorCollector())
    # 錯誤已由 collector 收進報告；不往上傳，免得 traceback 洗掉進度輸出。
    logger.propagate = False


async def timed_call(coro_factory) -> tuple[float, object, list[str]]:
    """執行一次查詢，回傳 (秒數, 結果, 這次呼叫期間 repository 記下的錯誤)。"""
    bucket: list[str] = []
    token = _errors.set(bucket)
    try:
        start = time.perf_counter()
        result = await coro_factory()
        return time.perf_counter() - start, result, bucket
    finally:
        _errors.reset(token)


def is_timeout(message: str) -> bool:
    lowered = message.lower()
    return "timed out" in lowered or "timeout" in lowered


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 3),
        "mean": round(statistics.mean(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def check_local_resolution(cases: list[Case]) -> None:
    """每個科別、類型都要本地表解析得出來；否則正式程式會去問 LLM，本腳本不做這件事。"""
    problems = []
    for case in cases:
        problems += [
            f"{case.key}：科別「{d}」" for d in case.departments if resolve_department(d) is None
        ]
        if case.facility_type and resolve_facility_type(case.facility_type) is None:
            problems.append(f"{case.key}：類型「{case.facility_type}」")
    if problems:
        raise SystemExit(
            "以下說法本地表解析不出來，正式程式會交給 LLM；請換成本地認得的說法：\n  "
            + "\n  ".join(problems)
        )


def _call_factory(service: MedicalService, case: Case):
    lat, lng = LOCATIONS[case.location] if case.location else (None, None)
    if case.kind == "nearby":
        return lambda: service.find_nearby_hospitals(
            lat, lng, open_now=case.open_now, facility_type=case.facility_type
        )
    if case.kind == "department":
        return lambda: service.find_nearby_facilities_by_department(
            lat, lng, list(case.departments), open_now=case.open_now,
            facility_type=case.facility_type,
        )
    return lambda: service.find_facility_by_name(case.keyword, lat=lat, lng=lng)


def _describe(case: Case, result) -> dict:
    if case.kind == "name":
        facilities, count = result
        return {"count": count, "top1": facilities[0].name if facilities else None}
    return {
        "count": len(result.facilities),
        "reached_km": result.reached_meters // 1000 if result.facilities else None,
        "satisfied": result.satisfied,
        "open_now_fallback": result.open_now_fallback,
    }


async def run(runs: int, warmup: int) -> list[dict]:
    MongoDBManager.configure(settings.MONGODB_URI)
    install_db_error_collector()
    # 不接 department_resolver／facility_type_resolver：保證不呼叫 LLM。
    service = MedicalService()
    rows: list[dict] = []
    for case in CASES:
        call = _call_factory(service, case)
        # 暖身：第一次要建連線、選 server，不列入統計。
        for _ in range(warmup):
            await timed_call(call)
        for run_index in range(1, runs + 1):
            seconds, result, errors = await timed_call(call)
            row = {
                "case": case.key,
                "run": run_index,
                "seconds": round(seconds, 3),
                "ok": not errors,
                "timeout": any(is_timeout(e) for e in errors),
                **_describe(case, result),
            }
            if errors:
                row["errors"] = errors
            rows.append(row)
            status = f"{seconds:.3f}s，{row['count']} 筆" if row["ok"] else f"失敗 {errors[0]}"
            print(f"  [{case.key}] 第 {run_index} 次  {status}", flush=True)
    return rows


def _summaries(rows: list[dict]) -> list[dict]:
    summaries = []
    for case in CASES:
        mine = [r for r in rows if r["case"] == case.key]
        ok = [r for r in mine if r["ok"]]
        last = ok[-1] if ok else {}
        summaries.append(
            {
                "case": case.key,
                "kind": case.kind,
                "label": case.label,
                "location": case.location,
                "failures": len(mine) - len(ok),
                "timeouts": sum(r["timeout"] for r in mine),
                "latency_seconds": _stats([r["seconds"] for r in ok]),
                "count": last.get("count"),
                "reached_km": last.get("reached_km"),
                "satisfied": last.get("satisfied"),
                "open_now_fallback": last.get("open_now_fallback"),
            }
        )
    return summaries


KIND_TITLES = {"nearby": "不分科", "department": "依科別", "name": "依名稱"}


def _markdown(meta: dict, summaries: list[dict]) -> str:
    lines = [
        f"# 醫療院所查詢回應時間 {meta['started_at']}",
        "",
        f"- 執行環境：**{meta['env_label']}**（{meta['platform']}，Python {meta['python']}）",
        f"- 資料庫：`{meta['database']}` 的 `medicalFacilities`，唯讀",
        f"- 每個案例 {meta['runs']} 次（另有 {meta['warmup']} 次暖身不列入），依序執行、不併發",
        "- 量的是 MedicalService 從呼叫到拿回院所清單；不含 Gemini agent 選工具、Flex 卡片與 LINE 推播。",
        "- 不呼叫 Gemini：科別與類型都由本地表解析，MedicalService 沒接 LLM 兜底。",
        "- 「現在有營業」的結果隨執行時刻而變；「放寬到」是最後一次湊滿或放棄時的搜尋半徑。",
        "- 失敗＝repository 記下 DB 錯誤（正式程式此時回空清單）；逾時是其中訊息含 timeout 的。",
    ]
    for kind, title in KIND_TITLES.items():
        mine = [s for s in summaries if s["kind"] == kind]
        lines += ["", f"## {title}", ""]
        if kind == "name":
            lines += [
                "| 案例 | 地點 | 筆數 | 中位數 | 平均 | 最快 | 最慢 | 失敗 | 逾時 |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        else:
            lines += [
                "| 案例 | 地點 | 筆數 | 放寬到 | 湊滿 | 中位數 | 平均 | 最快 | 最慢 | 失敗 | 逾時 |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        for s in mine:
            lat = s["latency_seconds"]
            fmt = (lambda key: f"{lat[key]:.3f}s") if lat.get("n") else (lambda key: "—")
            timing = f"{fmt('median')} | {fmt('mean')} | {fmt('min')} | {fmt('max')}"
            place = s["location"] or "—"
            count = "—" if s["count"] is None else s["count"]
            if kind == "name":
                lines.append(
                    f"| {s['label']} | {place} | {count} | {timing} "
                    f"| {s['failures']} | {s['timeouts']} |"
                )
            else:
                reached = "—" if s["reached_km"] is None else f"{s['reached_km']} km"
                satisfied = {True: "是", False: "否", None: "—"}[s["satisfied"]]
                if s["open_now_fallback"]:
                    satisfied += "（無營業中，退回全部）"
                lines.append(
                    f"| {s['label']} | {place} | {count} | {reached} | {satisfied} | {timing} "
                    f"| {s['failures']} | {s['timeouts']} |"
                )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5, help="每個案例量幾次（預設 5）")
    parser.add_argument(
        "--warmup", type=int, default=1, help="每個案例的暖身次數，不列入統計（預設 1）"
    )
    parser.add_argument(
        "--env-label", required=True, help="執行環境，寫進報告與摘要，例如 local、gcp-pod"
    )
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "evals" / "facility_search" / "reports"),
        help="報告輸出資料夾（預設 evals/facility_search/reports）",
    )
    args = parser.parse_args()

    check_local_resolution(CASES)
    started = datetime.now(TAIPEI)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(
        f"醫療院所查詢量測：{args.env_label}，{len(CASES)} 個案例，"
        f"每個 {args.runs} 次＋暖身 {args.warmup} 次（唯讀，不呼叫 Gemini）"
    )
    rows = asyncio.run(run(args.runs, args.warmup))
    summaries = _summaries(rows)

    meta = {
        "started_at": started.isoformat(timespec="seconds"),
        "env_label": args.env_label,
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
        "database": settings.MONGODB_DB,
        "runs": args.runs,
        "warmup": args.warmup,
        "name_search_radius_meters": NAME_SEARCH_RADIUS_METERS,
        "locations": LOCATIONS,
        "measures": "MedicalService 呼叫到拿回院所清單；不含 agent 選工具、Flex 卡片與 LINE 推播",
    }
    cases = [case.__dict__ for case in CASES]
    report = {"meta": meta, "cases": cases, "summary": summaries, "runs": rows}

    json_path = out / f"facility-search-{args.env_label}-{stamp}.json"
    md_path = out / f"facility-search-{args.env_label}-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(meta, summaries), encoding="utf-8")
    print(f"\n{_markdown(meta, summaries)}")
    print(f"報告：{json_path}\n摘要：{md_path}")


if __name__ == "__main__":
    main()
