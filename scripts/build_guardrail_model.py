#!/usr/bin/env python3
"""訓練 guardrail 分類器，產出執行期用的權重表。

**這是建置期工具**，與 `scripts/build_drug_catalog.py` 同一個模式：重的套件
（scikit-learn）留在 dev 群組，產出物是 `resources/guardrail_model.json`
——一張「字元片段 → 權重」的表。執行期只做查表與加總，不需要 sklearn、
numpy 或任何模型檔，`uv sync --no-dev` 也不會把它們裝進正式映像。

**為什麼是字元 n-gram TF-IDF + 邏輯回歸，而不是 transformer**：

1. 中文不必斷詞，對錯字與台語混用（「金罵」「雪壓」）比詞層級穩健。
2. RAGRouter-Bench（arXiv:2604.03455）在 7,727 條查詢的路由任務上量到
   TF-IDF + SVM 勝過語意 embedding 3.1 個 macro-F1——**但那是查詢複雜度
   路由，與本任務的主題分類不同**，所以這裡把它當可比較的基線而不是結論。
   若基線不足，下一步是換成 embedding + 同一個線性分類頭，資料與評測不變。
3. 產出物是幾 MB 的 JSON，可解釋（可以直接讀出模型憑哪些片段判斷），
   而醫療場景出事時「查得出來為什麼」是硬需求。

**輸出的門檻是給串接用的，不是給取代用的。** `low`／`high` 之外才由本地
判定，落在中間就升級給 Gemini。門檻以「漏判優先」的方式選：先固定可接受的
漏判率上限，再取在該限制下能讓最多流量留在本地的那組值。理由是兩種錯誤
代價不對稱——漏判會讓使用者問醫療問題卻得不到知識庫的答案，誤判只是白掛
一個工具。

用法（專案根目錄，需先 source .venv）：
  python scripts/build_guardrail_model.py
  python scripts/build_guardrail_model.py --max-miss-rate 0.005 --out /tmp/m.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline

DEFAULT_DATASET = _PROJECT_ROOT / "evals" / "guardrail" / "dataset.jsonl"
DEFAULT_OUT = _PROJECT_ROOT / "resources" / "guardrail_model.json"

# 字元 2–4 gram。下限 2 而不是 1：單字元片段（「的」「了」）幾乎命中所有
# 訊息，對分類沒有貢獻卻讓詞彙表暴增。上限 4 是成本與覆蓋的折衷——「健保局」
# 「量血壓」這種關鍵訊號多半落在 2–4 個字。
NGRAM_RANGE = (2, 4)

# 詞彙表上限。超過這個數量後多出來的都是只出現一兩次的長尾片段，對泛化沒有
# 幫助，只會把產出的 JSON 撐大（執行期要整份載進記憶體）。
MAX_FEATURES = 60_000

# 片段至少要在兩則訊息裡出現過。只出現一次的片段學到的是那一則的特例。
MIN_DF = 2


def _load(path: Path) -> tuple[list[str], list[int], list[str], list[str]]:
    texts: list[str] = []
    labels: list[int] = []
    splits: list[str] = []
    buckets: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        texts.append(row["text"])
        labels.append(int(row["label"]))
        splits.append(row.get("split", "train"))
        buckets.append(row.get("bucket", ""))
    return texts, labels, splits, buckets


def _pick_thresholds(
    probs: np.ndarray,
    labels: np.ndarray,
    max_miss_rate: float,
    max_false_alarm_rate: float,
) -> tuple[float, float, dict[str, Any]]:
    """選出 (low, high)：本地只在這兩個值之外下判斷。

    `high` 是「敢直接說是」的下限，`low` 是「敢直接說不是」的上限。

    兩者用不同的準則，因為兩種錯誤的代價不同：
      - `low` 受漏判率上限約束——在本地說「不是」而其實是醫療問題，使用者
        就拿不到知識庫的答案。取滿足上限的**最大**值，讓盡量多的負例留在本地。
      - `high` 受誤判率上限約束，而那個上限刻意放得比漏判寬。在本地說
        「是」而其實無關，後果只是把 RAG 工具掛給 agent——agent 未必會用，
        用了也只是多一次檢索。拿零誤判當條件會把門檻推到接近 1，讓幾乎所有
        流量都升級給 Gemini，等於白做。
    """
    order = np.argsort(probs)
    sorted_probs = probs[order]
    sorted_labels = labels[order]

    positives = int(labels.sum())
    best_low = 0.0
    for candidate in np.unique(sorted_probs):
        missed = int(((probs < candidate) & (labels == 1)).sum())
        if positives and missed / positives > max_miss_rate:
            break
        best_low = float(candidate)

    negatives = int((labels == 0).sum())
    best_high = 1.0
    for candidate in np.unique(sorted_probs)[::-1]:
        false_alarms = int(((probs >= candidate) & (labels == 0)).sum())
        if negatives and false_alarms / negatives > max_false_alarm_rate:
            break
        best_high = float(candidate)

    if best_high <= best_low:
        # 兩端交錯代表這份資料上不存在「兩邊都乾淨」的區間，退回只用 low。
        best_high = 1.0

    local = int(((probs < best_low) | (probs >= best_high)).sum())
    stats = {
        "local_share": local / len(probs) if len(probs) else 0.0,
        "escalate_share": 1 - (local / len(probs)) if len(probs) else 0.0,
        "missed_below_low": int(((probs < best_low) & (labels == 1)).sum()),
        "false_alarm_above_high": int(((probs >= best_high) & (labels == 0)).sum()),
    }
    return best_low, best_high, stats


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--max-miss-rate",
        type=float,
        default=0.002,
        help="本地直接說「不是」時可接受的漏判率上限（交叉驗證上量）",
    )
    parser.add_argument(
        "--max-false-alarm-rate",
        type=float,
        default=0.05,
        help=(
            "本地直接說「是」時可接受的誤判率上限。刻意比漏判寬——誤判只是"
            "白掛一個工具，漏判會讓使用者拿不到知識庫的答案"
        ),
    )
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args(argv)

    if not args.dataset.exists():
        print(f"找不到資料集：{args.dataset}", file=sys.stderr)
        return 2

    texts, labels, splits, buckets = _load(args.dataset)
    train_idx = [i for i, s in enumerate(splits) if s != "holdout"]
    hold_idx = [i for i, s in enumerate(splits) if s == "holdout"]
    X_train = [texts[i] for i in train_idx]
    y_train = np.array([labels[i] for i in train_idx])
    X_hold = [texts[i] for i in hold_idx]
    y_hold = np.array([labels[i] for i in hold_idx])
    print(f"train {len(X_train)} / holdout {len(X_hold)}")

    pipeline = Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    analyzer="char",
                    ngram_range=NGRAM_RANGE,
                    min_df=MIN_DF,
                    max_features=MAX_FEATURES,
                    lowercase=True,
                    sublinear_tf=False,
                    norm="l2",
                ),
            ),
            (
                "clf",
                # liblinear：資料量小、特徵稀疏時穩定，且不需要額外的收斂調參。
                LogisticRegression(C=4.0, max_iter=2000, solver="liblinear"),
            ),
        ]
    )

    # 門檻要在「模型沒看過的資料」上選，否則會樂觀。用交叉驗證的 out-of-fold
    # 機率而不是 holdout：holdout 要留給最後的誠實評測，一份資料不能既拿來
    # 選門檻又拿來宣稱效果。
    cv = StratifiedKFold(n_splits=args.folds, shuffle=True, random_state=20260909)
    oof = cross_val_predict(pipeline, X_train, y_train, cv=cv, method="predict_proba")[:, 1]
    print(f"交叉驗證 macro-F1（門檻 0.5）：{f1_score(y_train, oof >= 0.5, average='macro'):.4f}")

    low, high, stats = _pick_thresholds(
        oof, y_train, args.max_miss_rate, args.max_false_alarm_rate
    )
    print(
        f"門檻：low={low:.4f} high={high:.4f}  "
        f"本地解掉 {stats['local_share']:.1%}、升級 {stats['escalate_share']:.1%}"
    )

    pipeline.fit(X_train, y_train)

    if len(X_hold):
        hold_probs = pipeline.predict_proba(X_hold)[:, 1]
        macro = f1_score(y_hold, hold_probs >= 0.5, average="macro")
        local_mask = (hold_probs < low) | (hold_probs >= high)
        missed = int(((hold_probs < low) & (y_hold == 1)).sum())
        print(
            f"holdout：macro-F1 {macro:.4f}  本地解掉 {local_mask.mean():.1%}  "
            f"本地漏判 {missed} 則"
        )

    model = export_model(pipeline, thresholds=(low, high), stats=stats,
                         dataset=args.dataset, n_train=len(X_train))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    size_mb = args.out.stat().st_size / 1024 / 1024
    kept = model["terms"]
    print(f"\nwrote {args.out}  ({len(kept)} 個片段, {size_mb:.1f} MB)")

    ranked = sorted(kept.items(), key=lambda kv: kv[1][1])
    print("\n最能代表「不相關」的片段：", "、".join(t for t, _ in ranked[:12]))
    print("最能代表「健康醫療」的片段：", "、".join(t for t, _ in ranked[-12:]))
    return 0


def export_model(
    pipeline: Pipeline,
    *,
    thresholds: tuple[float, float],
    stats: dict[str, Any],
    dataset: Path,
    n_train: int,
) -> dict[str, Any]:
    """把訓練好的 pipeline 攤平成執行期的權重表。

    抽成獨立函式是為了讓 parity 測試能拿它與 `LocalGuardrailClassifier`
    對照——測試若自己重寫一次匯出邏輯，就驗不到匯出本身寫錯的情況。
    """
    vec: TfidfVectorizer = pipeline.named_steps["tfidf"]
    clf: LogisticRegression = pipeline.named_steps["clf"]
    coef = clf.coef_[0]
    vocabulary = vec.vocabulary_
    idf = vec.idf_
    low, high = thresholds

    # **整份詞彙表都要留，不能依權重修剪。** TfidfVectorizer 的 L2 正規化
    # 分母是「這則訊息命中的所有詞彙表片段」的平方和——砍掉權重接近 0 的
    # 片段不只是少了那一項的貢獻，而是改變了分母，於是每一筆分數都被整體
    # 縮放。那會讓執行期與訓練時的機率對不起來，也讓門檻失去意義。
    #
    # 代價是 JSON 大一些；那是正確性換體積，換得值得。
    kept = {
        term: [float(idf[index]), float(coef[index])]
        for term, index in vocabulary.items()
    }

    return {
        "format": "char-tfidf-logreg-v1",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset),
        "n_train": n_train,
        "ngram_range": list(NGRAM_RANGE),
        "lowercase": True,
        "intercept": float(clf.intercept_[0]),
        "n_documents": n_train,
        "thresholds": {"low": low, "high": high},
        "threshold_stats": stats,
        # {片段: [idf, 權重]}。兩個值放同一個陣列而不是兩張表：執行期是逐
        # 片段查一次，分開存會查兩次 dict 並多一份 key 的記憶體。
        "terms": kept,
    }


if __name__ == "__main__":
    raise SystemExit(main())
