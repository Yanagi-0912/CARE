"""本地 guardrail 分類器：字元 n-gram TF-IDF + 邏輯回歸的推論端。

**執行期零依賴。** 模型是 `resources/guardrail_model.json` 裡的一張
「字元片段 → (idf, 權重)」的表，由建置期的 `scripts/build_guardrail_model.py`
以 scikit-learn 訓練後匯出；這裡只做查表、加總與一次 sigmoid，不需要
sklearn、numpy 或任何模型執行框架（Dockerfile 的 `uv sync --no-dev` 也不會
把它們裝進正式映像）。

**這個類別不自己決定放行與否，它回傳機率。** 三分的決策在
`CascadeGuardrail`：機率落在 `low`／`high` 之外才由本地判定，中間地帶升級
給 LLM。這個切分是刻意的——GuardChain（arXiv:2512.19011）量到便宜分類器在
分佈外會「高信心答錯」（F1 從 0.96 掉到 0.43 以下），而合成訓練資料與真實
長輩訊息之間必然存在分佈差距。信心不足就交給 LLM，是這個設計唯一的安全網。

計算必須與 `TfidfVectorizer` **逐位元對齊**，否則訓練時選出的門檻在執行期
沒有意義。對齊的三個要點：

1. 只有詞彙表裡的片段參與計算（未見過的片段直接忽略，不是記 0 分後仍佔
   分母——sklearn 是先投影到詞彙表再算範數）。
2. L2 正規化的分母是「命中的所有詞彙表片段」的 tf×idf 平方和。這也是模型
   檔不能依權重修剪詞彙表的原因（見 build 腳本的註解）。
3. `lowercase=True` 與訓練一致；中文不受影響，但英文藥名（PANADOL）會。

`tests/unit/services/guardrail/test_local_classifier.py` 以實際模型檔比對
sklearn 的輸出，差異須小於 1e-9。
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_PATH = _PROJECT_ROOT / "resources" / "guardrail_model.json"

SUPPORTED_FORMAT = "char-tfidf-logreg-v1"

# 一段文字裡，詞彙表認得的片段佔不到這個比例，本地模型就不該自己下判斷。
#
# 機率只看「認得的片段」：分母是命中片段的範數，所以只認得一個片段時，那個
# 片段就拿走全部權重。2026-09-17 台語語音辨識出「恥笑漸漸光，咱就大聲仔想著煞」，
# guardrail 詞彙表只認得「就大」一個片段（佔 2.8%），而訓練資料裡含「就大」的
# 兩則剛好都是健康問題——整句靠這兩個字拿到 0.6046、越過 0.5989 被放行。這正是
# 模組註解說的分佈外高信心答錯。
#
# 5% 的依據（同日量測，本機算、不打 API）：正式環境 30 天 154 則使用者訊息裡，
# 低於 5% 而原本由本地判定的只有 3 則語音，全是辨識不通順的句子，而且急迫度那
# 邊本來就在問 LLM，使用者不會多等。三份資料集的 holdout（各約 2,000～2,700
# 則）裡，因此多等一次 LLM 的各 1 則。放寬到 10% 會開始收進「我得急性腸胃炎」
# 這類短句（字少，3、4 字片段多半沒見過），所以取 5%。
MIN_KNOWN_SHARE = 0.05


class LocalGuardrailClassifier:
    """讀入權重表，對一段文字回傳「與健康醫療相關」的機率。"""

    def __init__(self, model: dict[str, Any]) -> None:
        fmt = model.get("format")
        if fmt != SUPPORTED_FORMAT:
            raise ValueError(f"unsupported guardrail model format: {fmt!r}")
        self._terms: dict[str, list[float]] = model["terms"]
        self._intercept = float(model["intercept"])
        low, high = model["ngram_range"]
        self._ngram_range = (int(low), int(high))
        self._lowercase = bool(model.get("lowercase", True))
        thresholds = model.get("thresholds") or {}
        self.low = float(thresholds.get("low", 0.0))
        self.high = float(thresholds.get("high", 1.0))

    @classmethod
    def load(cls, path: Path | str = DEFAULT_MODEL_PATH) -> "LocalGuardrailClassifier":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(payload)

    def _ngrams(self, text: str) -> Counter[str]:
        """與 `TfidfVectorizer(analyzer="char")` 相同的切法。

        sklearn 對 `analyzer="char"` 是在**整串文字**上滑動視窗（不切詞、
        不去空白），所以這裡也不能先做任何清理——多做一步就對不齊了。
        """
        if self._lowercase:
            text = text.lower()
        counts: Counter[str] = Counter()
        length = len(text)
        for n in range(self._ngram_range[0], self._ngram_range[1] + 1):
            if length < n:
                continue
            for i in range(length - n + 1):
                counts[text[i : i + n]] += 1
        return counts

    def decision(self, text: str) -> float:
        """回傳邏輯回歸的線性分數（log-odds）。"""
        counts = self._ngrams(text or "")
        weighted: list[tuple[float, float]] = []
        norm_sq = 0.0
        for term, count in counts.items():
            entry = self._terms.get(term)
            if entry is None:
                continue
            idf, coef = entry
            value = count * idf
            norm_sq += value * value
            weighted.append((value, coef))
        if not weighted:
            # 一個詞彙表片段都沒命中：沒有任何證據，回傳純截距。
            return self._intercept
        norm = math.sqrt(norm_sq)
        total = sum(value * coef for value, coef in weighted) / norm
        return total + self._intercept

    def known_share(self, text: str) -> float:
        """文字切出的片段（含重複）有多少比例在詞彙表裡。"""
        counts = self._ngrams(text or "")
        total = sum(counts.values())
        if not total:
            return 0.0
        return sum(count for term, count in counts.items() if term in self._terms) / total

    def recognizes(self, text: str) -> bool:
        """認得的片段夠多，機率才有意義。不認得時呼叫端應交給 LLM，不要自己判。"""
        return self.known_share(text) >= MIN_KNOWN_SHARE

    def probability(self, text: str) -> float:
        score = self.decision(text)
        # 夾在 ±709：math.exp 在那之外會 OverflowError，而該範圍外的機率
        # 與 0／1 的差距遠小於任何門檻的解析度。
        clamped = max(-709.0, min(709.0, -score))
        return 1.0 / (1.0 + math.exp(clamped))
