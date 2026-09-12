"""排名融合：把多個 retriever 的結果合併為單一排名。

提供兩種融合函式，由 `HybridRetriever` 依設定選用：

**RRF（`reciprocal_rank_fusion`）** —— 只看名次，不看分數：

    score(doc) = Σ 1 / (k + rank_i(doc))

為什麼一開始選它：向量檢索的 cosine 相似度落在 0~1，Atlas Search 的 BM25
分數沒有上界且與整個語料庫的統計有關，兩者尺度不可比。RRF 只取「排名位置」，
因此天生免疫於尺度差異。k 越大，名次之間的差距越平緩；k=60 出自提出 RRF 的
原始論文（Cormack, Clarke & Büttcher, *Reciprocal Rank Fusion outperforms
Condorcet and individual Rank Learning Methods*, SIGIR 2009, pp. 758-759），
是它在 TREC 資料上用的值，**不是在本專案語料上校準過的值**。

**凸組合（`convex_combination_fusion`）** —— 先把各腿分數正規化到 [0,1]，
再加權相加：

    score(doc) = Σ wᵢ · normᵢ(doc)，   Σ wᵢ = 1

為什麼補上它：Bruch, Gai & Ingber 的 *An Analysis of Fusion Functions for
Hybrid Retrieval*（ACM TOIS 42(1), 2023；arXiv:2210.11934）在 in-domain 與
out-of-domain 兩種設定下都量到凸組合勝過 RRF，並指出 **RRF 對其參數敏感**，
而凸組合的權重「只需少量標註查詢」即可調出來（sample-efficient）。後面這點
是本專案採用它的關鍵——`evals/rag/golden.jsonl` 目前只有 55 題，不足以訓練
排序模型，但足以掃一個一維權重。

正規化選 min-max 而不是 z-score：BM25 分數的分佈右尾很長，z-score 會讓
少數極高分把其餘壓成一團；min-max 只保證「該腿的第一名是 1、最後一名是 0」，
不對分佈形狀做假設。

**兩個刻意的取捨，都會影響結果，改之前先看這裡：**

1. **只被單腿撈到的文件，另一腿記 `missing_score`（預設 0.0）。** 這是讓
   「兩腿都命中」浮上來的機制——RRF 靠名次相加天生有這個性質，凸組合要靠
   這個約定才有。設成 0.0 的代價是：某腿完全撈不到的好文件，最高只能拿到
   另一腿的權重上限；這正是 hybrid 想要的行為（兩邊都認同才是強證據），
   但它同時意味著權重調得極端時，弱勢那條腿的獨有結果會被壓到候選池外。
2. **某一腿的分數全部相同（含只回傳一筆）時，該腿全部記 1.0。** min-max 在
   這種退化情況下無定義；記 1.0 的語意是「這一腿對這批文件沒有鑑別度，但它們
   確實是這一腿撈到的」。記 0.0 會反過來懲罰「這一腿唯一撈到的那一筆」，那與
   取捨 1 的方向矛盾。
3. **凸組合沒有 RRF 那種「兩腿都命中就加分」的先天性質，這是兩者最實質的
   行為差異。** RRF 是名次倒數相加，只要出現在第二腿就一定變高；凸組合看的
   是分數，而 min-max 會把每一腿的**最後一名壓成 0**。後果是：一篇「vector
   墊底、text 第一」的文件，分數會等於一篇「vector 第一、text 根本沒撈到」的
   文件（各 0.5·1.0）。換句話說凸組合獎勵的是「**兩腿都給高分**」，不是
   「兩腿都出現」。這不是實作瑕疵，是這個融合函式的定義使然（Bruch et al.
   比較的也正是這個定義），但它會直接影響掃描結果怎麼解讀：alpha 調動的
   不只是比重，也在調「哪一腿的墊底文件被歸零」。

兩者的輸出 metadata 刻意對齊（同樣有 `score`／`fusion_score`／`retrievers`），
所以 `HybridRetriever` 之後的所有程式碼不需要知道用的是哪一種。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from langchain_core.documents import Document

DEFAULT_RRF_K = 60

# 凸組合的預設權重：兩腿各半。**這是「還沒調」的意思，不是量出來的最佳值**——
# 真正的值要用 scripts/rag_fusion_sweep.py 在 golden set 上掃出來再寫進 .env。
DEFAULT_FUSION_ALPHA = 0.5

FUSION_MODE_RRF = "rrf"
FUSION_MODE_CONVEX = "convex"
FUSION_MODES = (FUSION_MODE_RRF, FUSION_MODE_CONVEX)


def default_doc_key(doc: Document) -> str:
    """去重用的鍵。優先用 Mongo `_id`（retriever 已放進 metadata["id"]）。"""
    doc_id = str(doc.metadata.get("id") or "").strip()
    if doc_id:
        return doc_id
    return f"content:{(doc.page_content or '').strip()}"


def reciprocal_rank_fusion(
    ranked_lists: Sequence[tuple[str, Sequence[Document]]],
    *,
    k: int = DEFAULT_RRF_K,
    limit: int | None = None,
    doc_key: Callable[[Document], str] = default_doc_key,
) -> list[Document]:
    """融合多份已排序的結果。

    Args:
        ranked_lists: `(retriever 名稱, 已排序的 Document 列表)` 的序列。
            名稱只用於可觀測性（寫進 metadata），不影響分數。
        k: RRF 的平滑常數，必須為正。
        limit: 最多回傳幾筆；None 表示全部。
        doc_key: 判定「同一篇」的鍵函式。

    Returns:
        依融合分數遞減排序的新 Document 列表。同分時保留先出現者在前，
        確保結果穩定可測。

    輸出的 metadata：
        - `score`：融合分數。**刻意覆寫**原本的單一 retriever 分數，
          因為下游 `VectorScoreReranker` 是照 `score` 排序的，若留著
          尺度不可比的原始分數會排錯。
        - `rrf_score`：同上，語意明確的別名。
        - `rrf_ranks`：`{retriever 名稱: 名次}`。
        - `retrievers`：命中此篇的 retriever 名稱（依傳入順序）。
        - `<名稱>_score`：各 retriever 原本的分數，保留供除錯與分析。
    """
    if k <= 0:
        raise ValueError(f"RRF k must be positive, got {k}")

    accumulated: dict[str, dict[str, Any]] = {}

    for source_name, docs in ranked_lists:
        for rank, doc in enumerate(docs or [], start=1):
            key = doc_key(doc)
            entry = accumulated.get(key)
            if entry is None:
                entry = {
                    "doc": doc,
                    "score": 0.0,
                    "ranks": {},
                    "source_scores": {},
                    "order": len(accumulated),
                }
                accumulated[key] = entry

            entry["score"] += 1.0 / (k + rank)
            # 同一個 retriever 若重複給出同一篇，只採計最佳名次
            previous_rank = entry["ranks"].get(source_name)
            if previous_rank is None or rank < previous_rank:
                entry["ranks"][source_name] = rank
            original = doc.metadata.get("score")
            if isinstance(original, (int, float)):
                entry["source_scores"][f"{source_name}_score"] = float(original)

    ordered = sorted(
        accumulated.values(), key=lambda e: (-e["score"], e["order"])
    )
    if limit is not None:
        ordered = ordered[:limit]

    fused: list[Document] = []
    for entry in ordered:
        doc: Document = entry["doc"]
        metadata = {
            **doc.metadata,
            **entry["source_scores"],
            "score": entry["score"],
            "rrf_score": entry["score"],
            # 與 convex_combination_fusion 對齊的通用鍵：下游（日誌、eval、
            # 除錯腳本）不必先知道用了哪種融合才讀得到分數與模式。
            "fusion": FUSION_MODE_RRF,
            "fusion_score": entry["score"],
            "fusion_ranks": dict(entry["ranks"]),
            "rrf_ranks": dict(entry["ranks"]),
            "retrievers": list(entry["ranks"].keys()),
        }
        fused.append(Document(page_content=doc.page_content, metadata=metadata))
    return fused


def _min_max_normalize(values: Sequence[float]) -> list[float]:
    """把一腿的分數線性壓到 [0,1]。

    全部相同（含只有一筆）時回傳全 1.0——理由見模組 docstring 的取捨 2。
    """
    if not values:
        return []
    lowest = min(values)
    highest = max(values)
    span = highest - lowest
    if span <= 0:
        return [1.0] * len(values)
    return [(value - lowest) / span for value in values]


def _normalized_weights(
    names: Sequence[str],
    weights: Mapping[str, float] | None,
) -> dict[str, float]:
    """把使用者給的權重補齊並正規化成和為 1。

    刻意對「有 retriever 沒被指定權重」直接報錯而不是給預設值：這個函式
    的呼叫端是啟動時的組裝程式碼，權重打錯字的後果是整條檢索悄悄以錯誤的
    比重上線，而它在 golden set 上看起來只是「分數比較差」，不會炸。
    """
    if weights is None:
        if not names:
            return {}
        share = 1.0 / len(names)
        return {name: share for name in names}

    missing = [name for name in names if name not in weights]
    if missing:
        raise ValueError(f"missing fusion weight for: {', '.join(missing)}")

    selected = {name: float(weights[name]) for name in names}
    if any(value < 0 for value in selected.values()):
        raise ValueError(f"fusion weights must be non-negative, got {selected}")
    total = sum(selected.values())
    if total <= 0:
        raise ValueError("fusion weights must not sum to zero")
    return {name: value / total for name, value in selected.items()}


def convex_combination_fusion(
    ranked_lists: Sequence[tuple[str, Sequence[Document]]],
    *,
    weights: Mapping[str, float] | None = None,
    limit: int | None = None,
    doc_key: Callable[[Document], str] = default_doc_key,
    missing_score: float = 0.0,
) -> list[Document]:
    """以正規化分數的凸組合融合多份已排序的結果。

    Args:
        ranked_lists: `(retriever 名稱, 已排序的 Document 列表)` 的序列。
            與 RRF 不同，這裡的名稱**會影響分數**——它是查權重用的鍵。
        weights: `{retriever 名稱: 權重}`。不必自己加總為 1，函式會正規化；
            None 表示等權。少給任一腿的權重會直接 ValueError（見
            `_normalized_weights` 的註解）。
        limit: 最多回傳幾筆；None 表示全部。
        doc_key: 判定「同一篇」的鍵函式。
        missing_score: 某腿沒撈到這篇時，該腿記多少分（正規化後的尺度）。
            預設 0.0，理由見模組 docstring 的取捨 1。

    Returns:
        依融合分數遞減排序的新 Document 列表。同分時保留先出現者在前。

    輸出的 metadata（與 `reciprocal_rank_fusion` 對齊的部分刻意同名）：
        - `score`：融合分數，**覆寫**原本的單一 retriever 分數。理由同 RRF：
          下游 `VectorScoreReranker` 照 `score` 排序，留著尺度不可比的原始
          分數會排錯。
        - `fusion`：`"convex"`。
        - `fusion_score`：同 `score`。
        - `fusion_ranks`：`{retriever 名稱: 名次}`（只供觀測，不參與計分）。
        - `fusion_weights`：實際生效的正規化權重，寫進 metadata 是為了讓
          eval 報告能自證「這批結果是用哪組權重跑的」。
        - `retrievers`：命中此篇的 retriever 名稱。
        - `<名稱>_score`：該腿的原始分數。
        - `<名稱>_norm`：該腿正規化後的分數，供除錯時對照。
    """
    names = [name for name, _ in ranked_lists]
    effective_weights = _normalized_weights(names, weights)

    accumulated: dict[str, dict[str, Any]] = {}

    for source_name, docs in ranked_lists:
        docs = list(docs or [])
        raw_scores = [_document_score(doc) for doc in docs]
        normalized = _min_max_normalize(raw_scores)

        for rank, (doc, raw, norm) in enumerate(
            zip(docs, raw_scores, normalized), start=1
        ):
            key = doc_key(doc)
            entry = accumulated.get(key)
            if entry is None:
                entry = {
                    "doc": doc,
                    "ranks": {},
                    "norms": {},
                    "source_scores": {},
                    "order": len(accumulated),
                }
                accumulated[key] = entry

            # 同一個 retriever 重複給出同一篇時只採計最佳的那次，與 RRF
            # 「只採計最佳名次」的行為一致。
            previous_rank = entry["ranks"].get(source_name)
            if previous_rank is None or rank < previous_rank:
                entry["ranks"][source_name] = rank
                entry["norms"][source_name] = norm
                entry["source_scores"][f"{source_name}_score"] = raw

    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in accumulated.values():
        total = 0.0
        for name, weight in effective_weights.items():
            total += weight * entry["norms"].get(name, missing_score)
        scored.append((total, entry))

    scored.sort(key=lambda item: (-item[0], item[1]["order"]))
    if limit is not None:
        scored = scored[:limit]

    fused: list[Document] = []
    for total, entry in scored:
        doc: Document = entry["doc"]
        metadata = {
            **doc.metadata,
            **entry["source_scores"],
            **{f"{name}_norm": value for name, value in entry["norms"].items()},
            "score": total,
            "fusion": FUSION_MODE_CONVEX,
            "fusion_score": total,
            "fusion_ranks": dict(entry["ranks"]),
            "fusion_weights": dict(effective_weights),
            "retrievers": list(entry["ranks"].keys()),
        }
        fused.append(Document(page_content=doc.page_content, metadata=metadata))
    return fused


def _document_score(doc: Document) -> float:
    """取出這篇在該腿的原始分數；沒有數值就當 0.0。

    兩個 retriever 都保證會寫入數值 `score`（見 `retriever.py` 的投影），
    所以這裡的 0.0 是防禦而不是常態路徑。它與 min-max 搭配的效果是「沒有
    分數的那幾筆變成該腿的最低分」，不會讓整腿失效。
    """
    value = doc.metadata.get("score")
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0
