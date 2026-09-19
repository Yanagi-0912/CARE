"""本地分類器：字元 n-gram TF-IDF（＋句向量）+ 邏輯回歸的推論端。

guardrail、急迫度、走失、RAG 捷徑四個模型共用這一個類別，各自載入自己的
權重檔。

**兩種模型格式：**

* `char-tfidf-logreg-v1` —— **執行期零依賴**。模型是一張「字元片段 →
  (idf, 權重)」的表，這裡只做查表、加總與一次 sigmoid，不需要 sklearn、
  numpy 或任何模型執行框架。
* `char-tfidf-e5-logreg-v2` —— 在上面那張表之外，多一段 384 維的句向量
  權重，向量由 `text_encoder`（multilingual-e5-small int8 ONNX）產生。
  這個格式**需要** onnxruntime 與 tokenizers，正式映像會裝。

為什麼加句向量、為什麼是「加上去」而不是「換掉」、實測數字，見
`text_encoder` 的模組註解。簡短版：字面認的是「出現了哪些字」，句向量認的
是「這句話在講什麼」，兩者互補——2026-09-19 實測四個模型合併後全部變好，
但只用句向量有三個變差。

**這個類別不自己決定放行與否，它回傳機率。** 三分的決策在
`CascadeGuardrail`：機率落在 `low`／`high` 之外才由本地判定，中間地帶升級
給 LLM。這個切分是刻意的——GuardChain（arXiv:2512.19011）量到便宜分類器在
分佈外會「高信心答錯」（F1 從 0.96 掉到 0.43 以下），而合成訓練資料與真實
長輩訊息之間必然存在分佈差距。信心不足就交給 LLM，是這個設計唯一的安全網。

字元片段那一半的計算必須與 `TfidfVectorizer` **逐位元對齊**，否則訓練時選出
的門檻在執行期沒有意義。對齊的三個要點：

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

LOGGER_HEADER_TEXT = "[Services:LocalClassifier]"

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_PATH = _PROJECT_ROOT / "resources" / "guardrail_model.json"

SUPPORTED_FORMAT = "char-tfidf-logreg-v1"

# 加上句向量特徵後的格式。**刻意換一個字串而不是在 v1 上加一個可選欄位**：
# 舊版程式讀到新檔會直接拒絕，而不是默默忽略 dense 權重、算出一個少了一半
# 特徵卻看起來正常的分數——那種錯誤不會有任何訊息，門檻還會整個對不上。
DENSE_FORMAT = "char-tfidf-e5-logreg-v2"
SUPPORTED_FORMATS = (SUPPORTED_FORMAT, DENSE_FORMAT)

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


def _logit(p: float) -> float:
    """機率轉 log-odds。夾在 (0, 1) 開區間內，避免 log(0) 與除以 0。"""
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1.0 - p))


class LocalGuardrailClassifier:
    """讀入權重表，對一段文字回傳「與健康醫療相關」的機率。"""

    def __init__(self, model: dict[str, Any], encoder: Any | None = None) -> None:
        fmt = model.get("format")
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"unsupported guardrail model format: {fmt!r}")
        self._terms: dict[str, list[float]] = model["terms"]
        self._intercept = float(model["intercept"])
        low, high = model["ngram_range"]
        self._ngram_range = (int(low), int(high))
        self._lowercase = bool(model.get("lowercase", True))
        thresholds = model.get("thresholds") or {}
        self.low = float(thresholds.get("low", 0.0))
        self.high = float(thresholds.get("high", 1.0))

        # 句向量那一半。v1 的模型沒有這段，行為與以前完全相同。
        dense = model.get("dense") if fmt == DENSE_FORMAT else None
        if dense is None:
            self._dense_coef: list[float] | None = None
            self._dense_prefix = ""
            self._encoder = None
            return

        self._dense_coef = [float(v) for v in dense["coef"]]
        self._dense_prefix = str(dense.get("prefix", ""))
        # 編碼器沒注入就取共用的那一個。四個分類器共用同一個實例是這個設計
        # 划算的前提（見 text_encoder.get_shared_encoder）。
        if encoder is None:
            from app.services.guardrail.text_encoder import get_shared_encoder

            encoder = get_shared_encoder()
        self._encoder = encoder

    @classmethod
    def load(
        cls, path: Path | str = DEFAULT_MODEL_PATH, encoder: Any | None = None
    ) -> "LocalGuardrailClassifier":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(payload, encoder)

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
        """回傳邏輯回歸的線性分數（log-odds）。

        v2 的模型多一段句向量貢獻。訓練時兩種特徵是接在一起餵給同一個邏輯
        回歸（`hstack([tfidf_l2, e5_l2])`），所以執行期就是兩個點積相加——
        tfidf 那半照舊在命中片段上做 L2 正規化，e5 那半的向量本身已經是
        L2 正規化過的，直接點乘即可。
        """
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

        if weighted:
            norm = math.sqrt(norm_sq)
            sparse = sum(value * coef for value, coef in weighted) / norm
        else:
            # 一個詞彙表片段都沒命中：字面那半沒有任何證據，貢獻記 0。
            # （v1 在這裡直接回傳截距，等價於同一件事。）
            sparse = 0.0

        if self._dense_coef is None:
            return sparse + self._intercept

        try:
            vector = self._encoder.encode(text or "", self._dense_prefix)
        except Exception:  # noqa: BLE001
            # 編碼失敗時**不能**只回傳 sparse：那是一個少了一半特徵的分數，
            # 卻仍落在同一組門檻上比較，會被當成一次正常判斷。改為回傳一個
            # 保證落在 (low, high) 之間的分數，讓呼叫端照既有路徑升級給 LLM
            # ——與「認得太少就不判」是同一個處置。
            logger.exception("%s 句向量編碼失敗，本次交給 LLM", LOGGER_HEADER_TEXT)
            return _logit((self.low + self.high) / 2.0)

        dense = sum(v * c for v, c in zip(vector, self._dense_coef))
        return sparse + dense + self._intercept

    def known_share(self, text: str) -> float:
        """文字切出的片段（含重複）有多少比例在詞彙表裡。"""
        counts = self._ngrams(text or "")
        total = sum(counts.values())
        if not total:
            return 0.0
        return sum(count for term, count in counts.items() if term in self._terms) / total

    def recognizes(self, text: str) -> bool:
        """認得的片段夠多，機率才有意義。不認得時呼叫端應交給 LLM，不要自己判。

        **這道關卡刻意只看字元片段，不把句向量算進去。** 句向量對任何輸入都
        給得出一個向量，包括語音辨識吐出的不通順字串——那正是 2026-09-17
        台語那則誤放行的形狀（見 MIN_KNOWN_SHARE 的說明）。把它算進來會讓
        這道關卡對「模型其實看不懂」的輸入失去作用，而它的全部價值就在那裡。
        保持只看字面，最壞只是多交給 LLM 幾則，方向是安全的。
        """
        return self.known_share(text) >= MIN_KNOWN_SHARE

    def probability(self, text: str) -> float:
        score = self.decision(text)
        # 夾在 ±709：math.exp 在那之外會 OverflowError，而該範圍外的機率
        # 與 0／1 的差距遠小於任何門檻的解析度。
        clamped = max(-709.0, min(709.0, -score))
        return 1.0 / (1.0 + math.exp(clamped))
