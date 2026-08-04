"""Reciprocal Rank Fusion：把多個 retriever 的排名融合為單一排名。

為什麼用 RRF 而不是把分數相加：向量檢索的 cosine 相似度落在 0~1，
Atlas Search 的 BM25 分數沒有上界且與整個語料庫的統計有關，兩者尺度
不可比。RRF 只取「排名位置」，因此天生免疫於尺度差異。

    score(doc) = Σ 1 / (k + rank_i(doc))

k 越大，名次之間的差距越平緩；k=60 是文獻與各家實作的慣用值。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.documents import Document

DEFAULT_RRF_K = 60


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
            "rrf_ranks": dict(entry["ranks"]),
            "retrievers": list(entry["ranks"].keys()),
        }
        fused.append(Document(page_content=doc.page_content, metadata=metadata))
    return fused
