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

## 四個模型各自的訓練指令（專案根目錄，需先 source .venv）

**每個模型的門檻參數都不一樣，而且以前只散落在各自的 build_*_dataset.py 裡、
急迫度那個根本沒寫。** 2026-09-19 重訓時是靠比對線上模型的 threshold_stats
才反推回來的——那一趟很不值得，所以四條指令一起記在這裡：

  # guardrail：誤判只是白掛一個工具，放寬到 0.10 換取更多流量留在本地
  python scripts/build_guardrail_model.py --max-false-alarm-rate 0.10

  # 急迫度：--max-miss-rate 0，一則都不能在本地說「不緊急」
  python scripts/build_guardrail_model.py --dataset evals/urgency/dataset.jsonl \\
      --out resources/urgency_model.json --max-miss-rate 0

  # 走失求救：誤報會讓家人收到一則「他走丟了」的通報，所以誤判收得很緊
  python scripts/build_guardrail_model.py --dataset evals/lost/dataset.jsonl \\
      --out resources/lost_model.json --max-miss-rate 0.005 --max-false-alarm-rate 0.002

  # RAG 捷徑：誤送 RAG 會拿不到該有的卡片，誤判也收緊
  python scripts/build_guardrail_model.py --dataset evals/rag_route/dataset.jsonl \\
      --out resources/rag_route_model.json --max-miss-rate 0.002 \\
      --max-false-alarm-rate 0.0075 --exclude-from-thresholds everyday:

guardrail 用 0.10 而不是預設的 0.05：2026-09-15 併入外語資料（見
scripts/merge_guardrail_foreign.py）後，0.05 會把 high 推到 0.70，中文要問 LLM 的比例
從 17% 升到 25%；0.10 的 high 是 0.60，中文 19%、外語 17～25%，本地漏判仍是 0～2 則。
預設值不改，因為四個模型都用這支腳本，預設得是最保守的那一組。

急迫度的 `--max-miss-rate 0` 不是可選的：`tests/unit/services/medical/
test_urgency_local_model.py` 有一份 MUST_REACH_THE_LLM 清單（「我想跳樓」等），
用預設的 0.002 重訓會讓 low 從 0.0715 升到 0.1442，那些句子就會被本地判成
「不緊急」——那份測試就是為了擋住這件事而存在的。

## 特徵：字元片段，以及（預設開啟）句向量

2026-09-19 起預設會多算一段 multilingual-e5-small 的句向量接在字元片段後面。
為什麼、實測數字、為什麼是「加上去」而不是「換掉」，見
`app/services/guardrail/text_encoder.py` 的模組註解。用 `--no-dense` 可以
產出舊的 v1 格式（重現舊模型、或在沒有編碼器的環境訓練）。
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
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.pipeline import Pipeline
from scipy.sparse import csr_matrix, hstack

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


class _TextAndEmbedding(BaseEstimator, TransformerMixin):
    """以「列索引」當輸入，內部取原文做 TF-IDF，再把預先算好的句向量接在後面。

    為什麼要繞索引這一圈：交叉驗證每一折都必須重新 fit 向量器，否則詞彙表
    看過驗證折的文字，拿來選門檻的 out-of-fold 機率就會偏樂觀，而門檻正是
    這支腳本最重要的產出。sklearn 的 Pipeline 只會把 X 原樣往下傳，而這裡
    需要同時拿到「這一折的原文」與「對應那幾列的句向量」——用索引當 X 是讓
    兩者保持對齊最簡單的方式。

    句向量在外面一次算完（`_embed_splits`），不在每一折重算：它與標籤無關，
    重算只是把同一份結果再算五次。
    """

    def __init__(self, texts: list[str], embeddings: Optional[np.ndarray], max_features: int):
        self.texts = texts
        self.embeddings = embeddings
        self.max_features = max_features

    def fit(self, X, y=None):
        self.vectorizer_ = TfidfVectorizer(
            analyzer="char",
            ngram_range=NGRAM_RANGE,
            min_df=MIN_DF,
            max_features=self.max_features,
            lowercase=True,
            sublinear_tf=False,
            norm="l2",
        )
        self.vectorizer_.fit([self.texts[i] for i in np.asarray(X).ravel()])
        return self

    def transform(self, X):
        idx = np.asarray(X).ravel()
        sparse = self.vectorizer_.transform([self.texts[i] for i in idx])
        if self.embeddings is None:
            return sparse
        # 順序是「稀疏在前、稠密在後」，export_model 依這個順序切係數，
        # 執行期 local.py 也假設同一個順序。三處要一起改。
        return hstack([sparse, csr_matrix(self.embeddings[idx])]).tocsr()


def _embed_splits(
    texts: list[str], encoder_dir: Optional[Path]
) -> tuple[np.ndarray, dict[str, Any]]:
    """把全部文字轉成句向量，並回傳要寫進模型檔的中繼資料。

    用的是執行期同一支 `OnnxTextEncoder`（同一個模型檔、同一套 mean pooling
    與正規化、同一個前綴）。訓練與推論共用同一段程式，是這裡唯一能保證
    「訓練時的特徵」與「執行期的特徵」一致的方式——兩邊各寫一份遲早會漂。
    """
    from app.services.guardrail.text_encoder import (
        DEFAULT_MODEL_DIR,
        DEFAULT_PREFIX,
        OnnxTextEncoder,
    )

    model_dir = encoder_dir or DEFAULT_MODEL_DIR
    # 訓練是離線批次，執行緒可以多開；執行期的預設 1 是為了 pod 的 CPU 配額。
    encoder = OnnxTextEncoder(model_dir, threads=0)
    print(f"用 {model_dir} 算 {len(texts)} 筆句向量…", flush=True)
    vectors = np.array([encoder.encode(t, DEFAULT_PREFIX) for t in texts], dtype=np.float64)
    meta = {
        "model": "multilingual-e5-small-int8",
        "prefix": DEFAULT_PREFIX,
        "dim": int(vectors.shape[1]),
    }
    print(f"  完成 {vectors.shape}")
    return vectors, meta


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
    parser.add_argument(
        "--exclude-from-thresholds",
        default=None,
        metavar="BUCKET_PREFIX",
        help=(
            "bucket 以此開頭的列照樣參與訓練，但不參與門檻選擇。給「線上根本到不了"
            "這個分類器」的簡單負例用——算進誤判率分母只會把門檻壓得太寬"
            "（例：RAG 分流的 everyday:，已被 guardrail 擋掉）"
        ),
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument(
        "--no-dense",
        action="store_true",
        help=(
            "只用字元片段訓練，產出舊的 v1 格式。預設會加上句向量特徵——"
            "2026-09-19 四個模型實測合併後全部變好（見 text_encoder 模組註解），"
            "所以預設是加，這個開關是為了重現舊模型或在沒有編碼器的環境訓練。"
        ),
    )
    parser.add_argument(
        "--encoder-dir",
        type=Path,
        default=None,
        help="句向量模型目錄，預設用 app.services.guardrail.text_encoder 的預設路徑",
    )
    parser.add_argument(
        "--C",
        type=float,
        default=None,
        help=(
            "邏輯回歸的正則化強度。不給時：只用字元片段 4.0（上線值），"
            "加句向量 32.0（2026-09-19 在四個資料集上掃 8/32/128/512/2048，"
            "32 都是交叉驗證的峰值）。"
        ),
    )
    parser.add_argument(
        "--max-features",
        type=int,
        default=MAX_FEATURES,
        help=(
            "詞彙表上限。執行期整份載進記憶體（6 萬片段約 30 MB），任務窄的模型"
            "（走失求救）可以調小"
        ),
    )
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

    use_dense = not args.no_dense
    dense_meta: dict[str, Any] | None = None
    E_train = E_hold = None
    if use_dense:
        E_all, dense_meta = _embed_splits(texts, args.encoder_dir)
        E_train, E_hold = E_all[train_idx], E_all[hold_idx]

    C = args.C if args.C is not None else (32.0 if use_dense else 4.0)
    # liblinear：資料量小、特徵稀疏時穩定，且不需要額外的收斂調參。接上稠密
    # 向量之後矩陣不再稀疏，liblinear 會慢很多且沒有好處，改用 lbfgs。
    solver = "lbfgs" if use_dense else "liblinear"
    print(f"特徵：字元片段{'＋句向量' if use_dense else ''}  C={C:g}  solver={solver}")

    pipeline = Pipeline(
        [
            ("feat", _TextAndEmbedding(X_train, E_train, args.max_features)),
            ("clf", LogisticRegression(C=C, max_iter=4000, solver=solver)),
        ]
    )
    # 餵進去的是列索引不是原文，理由見 _TextAndEmbedding 的說明。
    idx_train = np.arange(len(X_train)).reshape(-1, 1)

    # 門檻要在「模型沒看過的資料」上選，否則會樂觀。用交叉驗證的 out-of-fold
    # 機率而不是 holdout：holdout 要留給最後的誠實評測，一份資料不能既拿來
    # 選門檻又拿來宣稱效果。
    cv = StratifiedKFold(n_splits=args.folds, shuffle=True, random_state=20260909)
    oof = cross_val_predict(pipeline, idx_train, y_train, cv=cv, method="predict_proba")[:, 1]
    print(f"交叉驗證 macro-F1（門檻 0.5）：{f1_score(y_train, oof >= 0.5, average='macro'):.4f}")

    if args.exclude_from_thresholds:
        keep = np.array(
            [not buckets[i].startswith(args.exclude_from_thresholds) for i in train_idx]
        )
        print(f"門檻只看 {int(keep.sum())} 筆（排除 bucket 前綴 {args.exclude_from_thresholds!r}）")
    else:
        keep = np.ones(len(train_idx), dtype=bool)
    low, high, stats = _pick_thresholds(
        oof[keep], y_train[keep], args.max_miss_rate, args.max_false_alarm_rate
    )
    print(
        f"門檻：low={low:.4f} high={high:.4f}  "
        f"本地解掉 {stats['local_share']:.1%}、升級 {stats['escalate_share']:.1%}"
    )

    pipeline.fit(idx_train, y_train)
    vectorizer = pipeline.named_steps["feat"].vectorizer_
    clf = pipeline.named_steps["clf"]

    if len(X_hold):
        # holdout 不能走 _TextAndEmbedding：那支的索引是對著 X_train。
        # 這裡直接用訓練好的向量器轉換，順序與 transform 一致。
        sparse_hold = vectorizer.transform(X_hold)
        A_hold = (
            sparse_hold
            if E_hold is None
            else hstack([sparse_hold, csr_matrix(E_hold)]).tocsr()
        )
        hold_probs = clf.predict_proba(A_hold)[:, 1]
        macro = f1_score(y_hold, hold_probs >= 0.5, average="macro")
        local_mask = (hold_probs < low) | (hold_probs >= high)
        missed = int(((hold_probs < low) & (y_hold == 1)).sum())
        print(
            f"holdout：macro-F1 {macro:.4f}  本地解掉 {local_mask.mean():.1%}  "
            f"本地漏判 {missed} 則"
        )

    # export_model 只讀 named_steps 的 tfidf 與 clf，所以這裡組一個扁平的
    # Pipeline 給它——不把 _TextAndEmbedding 傳進去，是為了讓匯出邏輯不必
    # 知道訓練期那套索引把戲。
    model = export_model(
        Pipeline([("tfidf", vectorizer), ("clf", clf)]),
        thresholds=(low, high),
        stats=stats,
        dataset=args.dataset,
        n_train=len(X_train),
        dense=dense_meta,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    size_mb = args.out.stat().st_size / 1024 / 1024
    kept = model["terms"]
    print(f"\nwrote {args.out}  ({len(kept)} 個片段, {size_mb:.1f} MB)")

    ranked = sorted(kept.items(), key=lambda kv: kv[1][1])
    print("\n最能代表負例（label 0）的片段：", "、".join(t for t, _ in ranked[:12]))
    print("最能代表正例（label 1）的片段：", "、".join(t for t, _ in ranked[-12:]))
    return 0


def export_model(
    pipeline: Pipeline,
    *,
    thresholds: tuple[float, float],
    stats: dict[str, Any],
    dataset: Path,
    n_train: int,
    dense: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把訓練好的 pipeline 攤平成執行期的權重表。

    抽成獨立函式是為了讓 parity 測試能拿它與 `LocalGuardrailClassifier`
    對照——測試若自己重寫一次匯出邏輯，就驗不到匯出本身寫錯的情況。

    *dense* 不是 None 時輸出 v2 格式：分類器的係數前 `len(vocabulary)` 個
    屬於字元片段、其餘屬於句向量。這個切法能成立是因為訓練時就是
    `hstack([tfidf, embeddings])`——稀疏那塊在前，順序與 `vocabulary_` 的
    索引一致。
    """
    vec: TfidfVectorizer = pipeline.named_steps["tfidf"]
    clf: LogisticRegression = pipeline.named_steps["clf"]
    coef = clf.coef_[0]
    vocabulary = vec.vocabulary_
    idf = vec.idf_
    low, high = thresholds

    dense_block: dict[str, Any] | None = None
    if dense is not None:
        n_sparse = len(vocabulary)
        dense_coef = coef[n_sparse:]
        if len(dense_coef) != dense["dim"]:
            raise ValueError(
                f"句向量係數數量不符：預期 {dense['dim']}，實得 {len(dense_coef)}。"
                f"多半是 hstack 的順序或詞彙表大小與訓練時不一致。"
            )
        dense_block = {
            "model": dense["model"],
            # 執行期必須用與訓練時相同的前綴，所以把它寫進模型檔，
            # 而不是讓兩邊各自從常數讀（那樣改了一邊不會有人發現）。
            "prefix": dense["prefix"],
            "dim": dense["dim"],
            "coef": [float(v) for v in dense_coef],
        }

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

    payload: dict[str, Any] = {
        "format": "char-tfidf-logreg-v1" if dense_block is None else "char-tfidf-e5-logreg-v2",
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
    if dense_block is not None:
        payload["dense"] = dense_block
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
