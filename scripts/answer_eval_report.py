#!/usr/bin/env python3
"""分析 scripts/answer_eval.py 的結果：CRAG 走哪條路、引用來源對不對、等多久。

用法（專案根目錄）：
  python scripts/answer_eval_report.py /tmp/answer.jsonl
  python scripts/answer_eval_report.py /tmp/answer.jsonl --titles-from-kb   # 有網址的來源到 Mongo 查標題（唯讀）

「引用到正解」看回答最後的來源清單：沒網址的來源會列「來源名｜標題」；有網址的只列網址，
要加 --titles-from-kb 才比得到標題。只算留在知識庫回答的題。
"""
import argparse
import json
import os
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("answers")
ap.add_argument("--golden", default=str(Path(__file__).resolve().parents[1] / "evals" / "rag" / "golden.jsonl"))
ap.add_argument("--titles-from-kb", action="store_true")
args = ap.parse_args()

gold = {}
for l in open(args.golden, encoding="utf-8"):
    if l.strip():
        d = json.loads(l)
        gold[d["id"]] = d
res = [json.loads(l) for l in open(args.answers, encoding="utf-8") if l.strip()]
url_title: dict[str, str] = {}
if args.titles_from_kb:
    from dotenv import load_dotenv
    from pymongo import MongoClient

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    col = MongoClient(os.environ["MONGODB_URI"])[os.environ["MONGODB_DB"]][os.environ["MONGODB_COLLECTION"]]
    urls = sorted({s["url"] for r in res for s in r["sources"] if s["url"]})
    for d in col.find({"url": {"$in": urls}}, {"url": 1, "original_title": 1}):
        url_title[d["url"]] = d["original_title"]


def kind(path):
    if not path:
        return "none"
    if path.startswith("kb"):
        return "kb"
    if path.startswith("web"):
        return "web"
    return path


def cited_hit(r, g):
    titles = g.get("expected_title_substrings") or []
    urls = g.get("expected_url_substrings") or []
    # 回答最後的「參考資料來源」清單才有「來源名｜標題」；SourceRef.label 是給 LINE 按鈕的短版，沒網址時只剩來源名
    listing = r["answer"].split("參考資料來源", 1)[1] if "參考資料來源" in r["answer"] else ""
    if any(t in listing for t in titles):
        return True
    for s in r["sources"]:
        if any(t in url_title.get(s["url"], "") for t in titles) or any(u in s["url"] for u in urls):
            return True
    return False


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0


groups = defaultdict(list)
for r in res:
    g = gold[r["id"]]
    groups[(g["route"], g.get("category") or "-")].append(r)

print("== 知識庫有答案的題（route kb）：CRAG 留在知識庫的比例、引用到正解的比例")
print(f"{'category':14}{'n':>5} {'留在kb':>7} {'轉網搜':>7} {'逾時/錯':>7} {'引用正解':>8} {'拒答':>6} {'p50秒':>6} {'p90秒':>6}")
tot = Counter()
for (route, cat), rs in sorted(groups.items()):
    if route != "kb":
        continue
    k = Counter(kind(r["path"]) if not r["error"] else "error" for r in rs)
    kb_rs = [r for r in rs if kind(r["path"]) == "kb"]
    ch = sum(cited_hit(r, gold[r["id"]]) for r in kb_rs)
    n = len(rs)
    ms = [r["ms"] for r in rs]
    print(f"{cat:14}{n:5} {k['kb']/n:7.1%} {k['web']/n:7.1%} {(n-k['kb']-k['web'])/n:7.1%} {ch/max(1,len(kb_rs)):8.1%} {sum(r['refused'] for r in rs)/n:6.1%} {statistics.median(ms)/1000:6.1f} {pct(ms,.9)/1000:6.1f}")
    tot.update({"n": n, "kb": k["kb"], "web": k["web"], "cited": ch})
print(f"{'全部':14}{tot['n']:5} {tot['kb']/tot['n']:7.1%} {tot['web']/tot['n']:7.1%}   引用正解（留在kb者）{tot['cited']/max(1,tot['kb']):.1%}")

print("\n== 知識庫沒答案的題（route web）：CRAG 轉網搜的比例（沒轉＝拿知識庫硬答）")
for (route, cat), rs in sorted(groups.items()):
    if route != "web":
        continue
    k = Counter(kind(r["path"]) if not r["error"] else "error" for r in rs)
    n = len(rs)
    print(f"  {cat:14} n={n:3} 轉網搜 {k['web']/n:6.1%}  知識庫作答 {k['kb']/n:6.1%}  其他 {(n-k['kb']-k['web'])/n:6.1%}  p50 {statistics.median(r['ms'] for r in rs)/1000:.1f}s")

print("\n== path 細分與等待時間")
bypath = defaultdict(list)
for r in res:
    bypath[r["path"] or ("error" if r["error"] else "none")].append(r["ms"])
for p, ms in sorted(bypath.items(), key=lambda x: -len(x[1])):
    print(f"  {p:28} n={len(ms):4} p50 {statistics.median(ms)/1000:5.1f}s p90 {pct(ms,.9)/1000:5.1f}s")

print("\n== 錯誤")
for r in res:
    if r["error"]:
        print(" ", r["id"], r["error"][:120])

print("\n== 知識庫題卻轉網搜（前 40 筆）")
for r in [r for r in res if gold[r["id"]]["route"] == "kb" and kind(r["path"]) == "web"][:40]:
    print(f"  {r['id']:10} {r['path']:24} top={r['top_rerank']} {gold[r['id']]['query'][:40]}")

print("\n== 該上網的題卻用知識庫答（前 40 筆）")
for r in [r for r in res if gold[r["id"]]["route"] == "web" and kind(r["path"]) == "kb"][:40]:
    srcs = "；".join(s["label"][:30] for s in r["sources"][:3])
    print(f"  {r['id']:12} top={r['top_rerank']} {gold[r['id']]['query'][:36]}｜引用：{srcs}")
