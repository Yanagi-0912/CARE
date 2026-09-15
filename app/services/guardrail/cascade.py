"""串接式 guardrail：本地分類器先判，沒把握才問 LLM。

**為什麼是串接而不是取代。** 本地模型是用 Gemini 生成的合成資料訓練的
（`scripts/build_guardrail_dataset.py`），而真實使用者是講台語混中文、會打
錯字的長輩——兩者之間必然有分佈差距。兩篇獨立的論文指向同一個失效模式：
GuardChain（arXiv:2512.19011）量到便宜分類器在分佈外會「高信心答錯」
（SVM 的 F1 從 0.96 掉到 0.43 以下）；CRAG 的 2026 復現（arXiv:2603.16169）
用 SHAP 發現 T5 評估器其實是靠命名實體對齊在判斷，跨領域即失效。

所以本地模型只在**兩端有把握時**下判斷，中間地帶一律升級給 LLM。門檻
（`low`／`high`）是建置期在交叉驗證的 out-of-fold 機率上選的，不是猜的。

門檻的不對稱也是刻意的：
  - `low` 由漏判率上限決定（預設 0.2%）。本地說「不是」而其實是醫療問題，
    使用者就拿不到知識庫的答案——**這是真傷害**。
  - `high` 由誤判率上限決定（guardrail 用 10%，比漏判寬 50 倍，理由見
    `scripts/build_guardrail_model.py`）。本地說「是」而其實無關，後果只是把
    RAG 工具掛給 agent，agent 未必會用——**這只是浪費**。

訓練資料涵蓋六種語言（外語併入方式見 `scripts/merge_guardrail_foreign.py`）。
2026-09-15 重訓後實測（holdout 2,066 筆）：本地解掉 78.9%，本地漏判 3 則；
其中中文 598 筆本地解掉 81%、漏判 0 則。只用中文訓練的舊模型把英文健康問題
198 題擋掉 195 題。

失效方向與 `GuardrailService` 一致：本地模型載入失敗時，`dependencies` 會
直接退回純 LLM 的版本；推論本身若拋例外，這裡升級給 LLM 而不是擅自放行。
"""

from __future__ import annotations

import logging

from app.core.request_logging import log_stage
from app.services.guardrail.local import LocalGuardrailClassifier
from app.services.guardrail.service import GuardrailService, is_location_message

logger = logging.getLogger(__name__)


class CascadeGuardrailService:
    """與 `GuardrailService` 同介面，可直接替換注入。"""

    def __init__(
        self,
        *,
        local: LocalGuardrailClassifier,
        fallback: GuardrailService,
    ) -> None:
        self._local = local
        self._fallback = fallback

    async def allow_rag_tool(self, user_text: str) -> bool:
        # 位置訊息走與 GuardrailService 相同的決定性規則。這條不能交給模型：
        # 座標文字若被判成「相關」，它會被當成 RAG 查詢送出去。
        if is_location_message(user_text):
            logger.debug("檢測到位置訊息，跳過分類並禁用 RAG。")
            return False

        try:
            probability = self._local.probability(user_text)
        except Exception:
            # 本地推論不該有例外，但真的有的話，升級給 LLM 而不是自己決定。
            logger.exception("本地 guardrail 推論失敗，升級給 LLM")
            return await self._fallback.allow_rag_tool(user_text)

        if probability >= self._local.high:
            log_stage(logger, "guardrail_local", outcome="allow", p=round(probability, 4))
            return True
        if probability < self._local.low:
            log_stage(logger, "guardrail_local", outcome="deny", p=round(probability, 4))
            return False

        log_stage(logger, "guardrail_local", outcome="escalate", p=round(probability, 4))
        return await self._fallback.allow_rag_tool(user_text)
