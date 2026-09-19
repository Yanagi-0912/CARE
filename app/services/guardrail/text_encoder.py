"""多語句向量編碼器（multilingual-e5-small，int8 ONNX）。

## 它補的是什麼

本地那幾個分類器原本只看字元片段（`local.py` 的 TF-IDF 權重表）。那種表認得
的是「出現了哪些字」，不是「這句話在講什麼」：「肚子」有權重、「腹痛」是另一
個毫不相干的格子，英文句子命中的則是 `'ch'`、`'av'` 這種沒有意義的字母碎片。

2026-09-19 實測（四個模型各自的 holdout，前後用**完全相同**的門檻參數，
只有特徵不同）：

| 模型      | macro-F1        | 本地解掉        | 本地漏判 |
|-----------|-----------------|-----------------|----------|
| guardrail | 0.9099 → 0.9538 | 78.9% → 92.3%   | 3 → 2    |
| urgency   | 0.9671 → 0.9782 | 73.1% → 82.8%   | 0 → 0    |
| lost      | 0.9653 → 0.9832 | 94.2% → 96.4%   | 2 → 2    |
| rag_route | 0.8882 → 0.9194 | 40.8% → 53.6%   | 4 → 2    |

「本地解掉」是這裡最值錢的一欄：落在門檻之間的訊息要多打一次 LLM，
而 rag_route 那一次是 agent 選工具（中位數 3.2 秒）。

## 為什麼是「加上去」而不是「換掉」

同一批實測裡，**只用向量**四個有三個變差（urgency 0.9671→0.9468、
lost 0.9653→0.9547、rag_route 0.8882→0.8616），只有 guardrail 變好。
原因是 urgency／lost 要認的是一組相對封閉的訊號詞（昏倒、叫不醒、不知道我
在哪），字面比對本來就夠強；只有 guardrail 的「跟健康醫療有沒有關」是開放
集合，列舉不完。兩種特徵接在一起才四個全贏——字面與語意互補，不是替代。

所以這支不取代 `local.py`，是餵給它的第二種特徵。

## 為什麼是 int8

同一份資料 fp32 的 macro-F1 是 0.9455、int8 是 0.9428，差 0.003；但模型檔
118MB vs 470MB、常駐記憶體 280MB vs 928MB。這個差價不值得。

## 為什麼關掉 ONNX 的記憶體池

`enable_cpu_mem_arena=True`（預設）實測常駐 516MB，關掉是 280MB，延遲從
1.5ms 變 1.9ms。backend pod 閒置本來就約 500Mi，省下的 236MB 比那 0.4ms 值錢。

## 執行緒數為什麼預設 1

backend 的 CPU 上限是 500m（半顆核心，見 CARE-infra values.yaml）。在 CFS
配額之下開多執行緒不會變快，只會讓工作被切碎後互相等待、還更容易被節流。
訓練時（`scripts/build_guardrail_model.py`）才傳 `threads=0`（用滿所有核心）
——那是離線批次，沒有配額問題。

**本機實測的延遲（4 執行緒、無配額）不能直接套到 pod 上**：單句 1.9ms 是在
Mac 上量的，care-vm 是 x86 且只有 500m 配額，上線後要在 VM 重量一次。
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Services:TextEncoder]"

# 模型目錄。Dockerfile 在 build 時下載到這裡；本機開發用
# `python scripts/fetch_text_encoder.py` 取得。
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "multilingual-e5-small"
ONNX_FILENAME = "model_quantized.onnx"
TOKENIZER_FILENAME = "tokenizer.json"

# e5 系列要求輸入帶前綴，分類用途一律 "query: "。這個值同時寫進模型 JSON
# 的 dense.prefix，執行期以那邊為準——訓練時用什麼前綴，執行期就得用什麼。
DEFAULT_PREFIX = "query: "

# 超過這個 token 數就截斷。LINE 訊息幾乎都遠短於此；長訊息多出來的部分對
# 「這句話在講什麼」幾乎沒有增益，卻讓延遲隨長度線性成長。
MAX_TOKENS = 128

# 編碼快取的容量。見 `encode` 的說明：它存在的理由是「一則訊息被四個分類器
# 各查一次」，不是為了跨訊息重用，所以幾十筆就夠——32 筆約等於同時在處理
# 8 則訊息都還能全部命中。
CACHE_SIZE = 32


class TextEncoder(Protocol):
    def encode(self, text: str, prefix: str = DEFAULT_PREFIX) -> list[float]: ...


class OnnxTextEncoder:
    """ONNX Runtime 版的句向量編碼器。執行緒安全，可由多個分類器共用同一個實例。"""

    def __init__(self, model_dir: Path | str = DEFAULT_MODEL_DIR, *, threads: int = 1) -> None:
        # onnxruntime 與 tokenizers 只在這裡 import，不放模組頂端：
        # local.py 匯入本模組的型別時不該被迫載入這兩個套件，單元測試也才
        # 能在沒有模型檔的環境跑。
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._np = np
        model_dir = Path(model_dir)
        onnx_path = model_dir / ONNX_FILENAME
        tokenizer_path = model_dir / TOKENIZER_FILENAME
        for p in (onnx_path, tokenizer_path):
            if not p.exists():
                raise FileNotFoundError(
                    f"缺少句向量模型檔 {p}。正式映像由 Dockerfile 在 build 時下載；"
                    f"本機請執行 python scripts/fetch_text_encoder.py"
                )

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        # 見模組 docstring：關掉記憶體池，用 0.4ms 換 236MB。
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False

        self._session = ort.InferenceSession(
            str(onnx_path), options, providers=["CPUExecutionProvider"]
        )
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._input_names = {i.name for i in self._session.get_inputs()}
        # ONNX Runtime 的 session 本身是執行緒安全的，但 tokenizer 的截斷設定
        # 是實例層級的狀態，所以編碼這段仍上鎖。單次呼叫是毫秒級，不會成為瓶頸。
        self._lock = threading.Lock()
        self._cache: "OrderedDict[tuple[str, str], list[float]]" = OrderedDict()
        self._cache_lock = threading.Lock()
        logger.info(
            "%s 載入完成 dir=%s threads=%d", LOGGER_HEADER_TEXT, model_dir, threads
        )

    def encode(self, text: str, prefix: str = DEFAULT_PREFIX) -> list[float]:
        """回傳 L2 正規化後的句向量（384 維）。同一則訊息重複要求時走快取。

        **快取不是效能微調，是這個設計成立的前提。** 一則訊息進來會被四個
        分類器各判一次（guardrail、急迫度、走失、RAG 捷徑），它們各自呼叫
        `encode`。沒有快取就等於同一段文字編碼四次——實測一則訊息的總延遲
        是 11.1ms，而編碼一次只要 1.9ms，多出來的全是重複工。
        """
        key = (prefix, text or "")
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit

        vector = self._encode_uncached(text, prefix)

        with self._cache_lock:
            self._cache[key] = vector
            # 容量只要夠裝「同時在處理的幾則訊息 × 4 次查詢」。開大沒有意義：
            # 使用者不會重複傳同一句話，快取命中幾乎都發生在同一則訊息之內。
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return vector

    def _encode_uncached(self, text: str, prefix: str) -> list[float]:
        np = self._np
        with self._lock:
            enc = self._tokenizer.encode(f"{prefix}{text or ''}")
            ids = enc.ids[:MAX_TOKENS]
            mask = enc.attention_mask[:MAX_TOKENS]

        input_ids = np.array([ids], dtype=np.int64)
        attention = np.array([mask], dtype=np.int64)
        feed: dict[str, Any] = {"input_ids": input_ids, "attention_mask": attention}
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)

        hidden = self._session.run(None, feed)[0]
        # mean pooling（只算有效 token）再 L2 正規化——e5 的標準用法，
        # 訓練時 scripts/build_guardrail_model.py 用的也是這一套。
        m = attention[:, :, None].astype(np.float32)
        pooled = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
        pooled = pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
        return pooled[0].astype(float).tolist()


_shared: Optional[OnnxTextEncoder] = None
_shared_lock = threading.Lock()


def get_shared_encoder(
    model_dir: Path | str = DEFAULT_MODEL_DIR, *, threads: int = 1
) -> OnnxTextEncoder:
    """取得四個本地分類器共用的那一個編碼器實例。

    共用而不是各自載入，是整個設計的關鍵：一則訊息進來，編碼跑一次、
    四個分類器各自拿同一個向量去點乘自己的權重。各自載入會是四份 280MB
    的常駐記憶體與四倍的延遲，那樣就不划算了。
    """
    global _shared
    if _shared is None:
        with _shared_lock:
            if _shared is None:
                _shared = OnnxTextEncoder(model_dir, threads=threads)
    return _shared
