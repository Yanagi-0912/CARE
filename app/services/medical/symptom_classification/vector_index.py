"""
症狀條目的語意向量索引。口語說法 → 表內條目的召回層。

為什麼是向量而不是別名表：
    見 openspec/changes/symptom-department-guidance/design.md 決策 12。
    簡述：754 條手寫別名只換到 48% 命中率，且六個跨語言樣本全數落空；
    向量在零別名下達到 86% top-1、100% top-3，跨語言直接可用。

召回，不是決選：
    實測的決定性反例是「眼壓高」——top1 是「高血壓」(0.942)，正解「青光眼」
    只有 0.897，margin 0.045。不是險勝，是有把握地錯，而且跨科。
    但正解 100% 落在 top-3，所以本模組只負責把候選縮到 k 個，
    由誰是正解交給 enum LLM 決定（normalizer._classify）。

為什麼不走 Atlas $vectorSearch：
    條目只有數百筆。$vectorSearch 是 ANN 近似搜尋，會讓門檻校準的數字不穩，
    還要在 Atlas 手動建第三個索引、每則訊息多一次網路往返。
    校準時 391 筆 × 768 維在記憶體暴力精確比對實測 4.6 ms，純 Python 即可，
    不需要 numpy。Mongo／檔案只負責持久化，不負責查詢。

為什麼綁 hash：
    tasks 2.2 的人工審定會逐條改寫 term。改完之後舊向量仍算得出分數、
    仍查得到條目，只是對到錯的東西——這種失敗完全無聲。因此載入時比對
    對照表內容的 hash，不符即拒用，寧可退回 LLM 全表兜底。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import struct
from dataclasses import dataclass
from operator import mul
from pathlib import Path
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Services:SymptomVectorIndex]"

# --- 校準常數。SHALL NOT 移到 .env，理由見 design 決策 12 -----------------------
#
# 這些值是對「gemini-embedding-001 × 這一版對照表」校準出來的，跨環境調整沒有
# 意義：改了門檻卻沒重算向量，只會靜默地對到錯的條目。它們與向量檔一起版控。

VECTOR_DIM = 768
"""實測 768 維與 3072 維同分（top1 36/42、top3 100%），記憶體 4.8MB → 1.2MB。"""

EMBEDDING_TASK_TYPE = "SEMANTIC_SIMILARITY"
"""不可沿用 RAG 的 RETRIEVAL_QUERY／RETRIEVAL_DOCUMENT 非對稱配對：那是為
「問句 → 文件段落」設計的，症狀比對是短語對短語。實測 top3 100% vs 90%。"""

AUTO_ACCEPT_SCORE = 0.95
"""直接採用 top-1 的門檻。實測此門檻下 28 筆自動採用全部正確；
放寬到 0.94 會讓「眼壓高 → 高血壓」(0.942) 溜進來。"""

MIN_MATCH_SCORE = 0.87
"""低於此值視為未命中，走保底。閒聊樣本上限 0.846，正樣本下限 0.889。"""

TOP_K = 5
"""交給 LLM 決選的候選數。實測正解 100% 落在 top-3，取 5 留餘裕。"""

DEFAULT_VECTOR_PATH = (
    Path(__file__).resolve().parents[4]
    / "resources"
    / "symptom_department_table"
    / "symptom_vectors.json"
)

# 2：加上 embedding_model。第 1 版的檔案沒有記模型，換成同維度的另一個模型時
# 每一道檢查都會通過、靜默比對不相容的向量，所以舊檔一律拒用、重建。
_FORMAT_VERSION = 2


@dataclass(frozen=True)
class Match:
    term: str
    score: float


def table_content_hash(terms: Sequence[str]) -> str:
    """對照表內容的指紋。條目改寫或增刪都會改變它。"""
    joined = "\n".join(terms).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


def normalize(vector: Sequence[float]) -> list[float]:
    """轉成單位向量，之後餘弦相似度就等於內積。"""
    length = math.sqrt(sum(map(mul, vector, vector)))
    if not length:
        return list(vector)
    return [x / length for x in vector]


def _pack(vector: Sequence[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")


def _unpack(blob: str, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", base64.b64decode(blob)))


class SymptomVectorIndex:
    """條目向量的記憶體索引。建構後純讀取。"""

    def __init__(
        self,
        *,
        terms: Sequence[str],
        vectors: Sequence[Sequence[float]],
        table_hash: str,
        dim: int = VECTOR_DIM,
        embedding_model: str = "",
    ) -> None:
        if len(terms) != len(vectors):
            raise ValueError(
                f"條目數 {len(terms)} 與向量數 {len(vectors)} 不符"
            )
        # 每一條都要檢查：search() 以 zip 逐維相乘，長度不符的向量會被靜默截斷、
        # 算出一個看似正常的分數，而不是像查詢向量維度不符那樣炸開。
        for position, vector in enumerate(vectors):
            if len(vector) != dim:
                raise ValueError(
                    f"第 {position} 條向量維度為 {len(vector)}，索引為 {dim}"
                )
        self._terms = tuple(terms)
        # 一律在此正規化，之後比對只做內積。來源是否已正規化不影響結果。
        self._vectors = [normalize(v) for v in vectors]
        self._table_hash = table_hash
        self._dim = dim
        self._embedding_model = embedding_model

    @property
    def terms(self) -> tuple[str, ...]:
        return self._terms

    @property
    def table_hash(self) -> str:
        return self._table_hash

    def search(self, query_vector: Sequence[float], k: int = TOP_K) -> tuple[Match, ...]:
        """回傳分數由高到低的前 k 個條目。全表暴力精確比對，校準時 391 筆實測 4.6 ms。"""
        query = normalize(query_vector)
        if len(query) != self._dim:
            raise ValueError(
                f"查詢向量維度為 {len(query)}，索引為 {self._dim}——"
                "必須用建索引時同一個模型與 output_dimensionality"
            )
        scored = sorted(
            (
                (sum(map(mul, query, vector)), term)
                for term, vector in zip(self._terms, self._vectors)
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return tuple(Match(term=term, score=score) for score, term in scored[:k])

    # --- 持久化 --------------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": _FORMAT_VERSION,
            "model_task_type": EMBEDDING_TASK_TYPE,
            "embedding_model": self._embedding_model,
            "dim": self._dim,
            "table_hash": self._table_hash,
            "terms": list(self._terms),
            # float32 + base64。同樣的資料存成 JSON 數字約 3MB，這裡約 1.6MB，
            # 且載入不必逐個 parse 浮點數字串。
            "vectors": [_pack(v) for v in self._vectors],
        }
        # 唯一的呼叫端是開發者手動執行的 scripts/build_symptom_vectors.py，path 來自
        # 它的 --out 參數或預設路徑，執行期不會把使用者輸入傳進來。SonarCloud 的
        # 路徑穿越規則把 CLI 參數當成不可信輸入，這裡不適用，故標 NOSONAR。
        path.write_text(  # NOSONAR：開發者 CLI 參數，非使用者輸入（見上）
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        logger.info(
            f"{LOGGER_HEADER_TEXT} 已寫入 %s（%d 條、%d 維）",
            path,
            len(self._terms),
            self._dim,
        )

    @classmethod
    def load(
        cls, path: Path, *, expected_hash: str, expected_model: str
    ) -> "SymptomVectorIndex | None":
        """
        載入向量檔。任何不一致都回 None 而不是拋錯——呼叫端會退回 LLM 全表
        兜底，那是可用的降級；帶著對不上的向量提供服務則會靜默給錯答案。
        """
        if not path.exists():
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 向量檔不存在（%s），"
                "比對層退回 LLM 全表兜底。請執行 scripts/build_symptom_vectors.py",
                path,
            )
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.error(f"{LOGGER_HEADER_TEXT} 向量檔讀取失敗，改用兜底", exc_info=True)
            return None

        if not isinstance(payload, dict):
            logger.error(
                f"{LOGGER_HEADER_TEXT} 向量檔最外層是 %s 而不是物件，改用兜底",
                type(payload).__name__,
            )
            return None

        if payload.get("format_version") != _FORMAT_VERSION:
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 向量檔格式版本為 %r，本版本需要 %r，拒用",
                payload.get("format_version"),
                _FORMAT_VERSION,
            )
            return None

        # 模型或 task_type 不同的向量仍是同維度的數字，其餘檢查都會通過，查詢卻是
        # 在比對兩個不相容的空間——分數看起來正常，對到的條目是錯的。
        if payload.get("model_task_type") != EMBEDDING_TASK_TYPE:
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 向量檔的 task_type 為 %r，查詢用 %r，拒用",
                payload.get("model_task_type"),
                EMBEDDING_TASK_TYPE,
            )
            return None
        if payload.get("embedding_model") != expected_model:
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 向量檔以 %r 建立，查詢用 %r，拒用。"
                "換模型後必須重跑 scripts/build_symptom_vectors.py",
                payload.get("embedding_model"),
                expected_model,
            )
            return None

        if payload.get("table_hash") != expected_hash:
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 向量檔與對照表不同步（檔內 hash %s，"
                "目前的表 %s），拒用並退回兜底。對照表改過就必須重跑 "
                "scripts/build_symptom_vectors.py",
                str(payload.get("table_hash"))[:12],
                expected_hash[:12],
            )
            return None

        try:
            # 第 2 版一律寫入 dim；缺了就是檔案不合法，與其他欄位缺漏同樣拒用。
            dim = int(payload["dim"])
            vectors = [_unpack(blob, dim) for blob in payload["vectors"]]
            return cls(
                terms=payload["terms"],
                vectors=vectors,
                table_hash=payload["table_hash"],
                dim=dim,
                embedding_model=payload["embedding_model"],
            )
        except Exception:  # noqa: BLE001
            logger.error(f"{LOGGER_HEADER_TEXT} 向量檔內容不合法，改用兜底", exc_info=True)
            return None


def build_index(
    terms: Sequence[str],
    embedded: Iterable[Sequence[float]],
    *,
    embedding_model: str,
) -> SymptomVectorIndex:
    """由已算好的向量組成索引。取向量的 I/O 留在呼叫端，本模組不打網路。"""
    vectors = list(embedded)
    if not vectors:
        raise ValueError("沒有任何向量可建索引")
    # 維度取自資料而非常數：這樣「向量是幾維」永遠是實際事實，
    # 常數改了但向量沒重算時，會在 search 的維度檢查被抓出來而不是靜默錯配。
    return SymptomVectorIndex(
        terms=terms,
        vectors=vectors,
        table_hash=table_content_hash(terms),
        dim=len(vectors[0]),
        embedding_model=embedding_model,
    )
