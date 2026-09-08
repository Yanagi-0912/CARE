"""
症狀 → 建議科別。流程：正規化 → 對照表 → 建議。

本服務只給「就診方向」，不做診斷：
    輸出一律是多候選（上限 3）加保底，並帶免責。原本 department_matcher 明列
    「不做症狀分診」的 Non-Goal，理由是猜錯會把需要急診的人導向一般門診——
    那條理由仍然成立，但它現在由別的元件負責：急迫度判斷（urgency.py）擋在
    整個 agent 之前，判定為緊急的訊息根本不會走到這裡。改變的是問題設定：
    使用者本來就會問，答案要走一條有安全邊界的路，而不是落到沒有邊界的自由生成。

為什麼要過濾兒科：
    對照表有 11 條症狀同時掛在兒科與成人科別（腹痛、發燒、咳嗽…），因為那些
    症狀大人小孩都會有。不過濾時，成人問「我肚子好痛要掛哪一科」會拿到
    「內科、兒科」——兒科那一項對他沒有意義，卻佔掉三個候選的其中一個。
    判斷依據是使用者填的年齡，加上訊息裡有沒有孩童指涉（家長幫小孩問時，
    年齡欄位是家長的）。兩者都不成立才濾掉，且兒科是唯一候選時一律保留——
    那代表這個症狀本來就只有兒科看（尿床、生長發育遲緩、新生兒照護）。

本服務不做急迫度判斷：
    急迫度是「要不要現在就去急診」，科別建議是「門診該掛哪一科」，兩者正交。
    前一版把急迫度檢查放在本服務裡，於是「我阿公昏迷」因為沒問科別、沒走到
    這個服務而完全跳過檢查。那個耦合已經拆掉，不要再加回來。

本服務不請求位置：
    使用者問的是「掛哪一科」，答完就是答完了。他若接著說「附近有嗎」，既有的
    科別意圖跨輪保留機制會自然接上，因為建議的科別已經在對話歷史裡。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.user_age import get_request_age, is_pediatric_age
from app.services.medical.symptom_classification.normalizer import (
    SymptomResolver,
    mentions_child,
)
from app.services.medical.symptom_classification.symptom_table import (
    MAX_CANDIDATES,
    DepartmentCandidate,
    SymptomTable,
)

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Services:SymptomTriage]"

# 保底建議。這兩科的職責本就包含初步評估與轉診，把不確定的人導到這裡是既有
# 醫療體系的設計，不是系統在猜。順序：家醫科在前，診所層級密度最高。
FALLBACK_DEPARTMENTS: tuple[str, ...] = ("家醫科", "內科")

# 兒科的 canonical 值。過濾用，不寫死在方法裡以免與對照表脫鉤。
PEDIATRIC_DEPARTMENT = "兒科"

RESULT_SUGGESTION = "suggestion"
RESULT_FALLBACK = "fallback"

@dataclass(frozen=True)
class SymptomTriageResult:
    kind: str
    """RESULT_SUGGESTION / RESULT_FALLBACK"""

    user_input: str

    # --- 建議 ---
    matched_term: str | None = None
    candidates: tuple[DepartmentCandidate, ...] = field(default_factory=tuple)
    fallback_reason: str | None = None

    @property
    def primary_department(self) -> str | None:
        return self.candidates[0].canonical if self.candidates else None


class SymptomDepartmentService:
    def __init__(
        self,
        *,
        table: SymptomTable,
        normalizer: SymptomResolver,
    ) -> None:
        self._table = table
        self._normalizer = normalizer

    async def suggest(self, text: str) -> SymptomTriageResult:
        term = await self._normalizer.resolve(text)
        if term is None:
            return self._fallback(text, "無法對應到已知的症狀條目")

        entry = self._table.lookup(term)
        if entry is None:
            # 正規化層的 enum 應該擋掉這種情況，走到這裡代表表與正規化層不同步。
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 正規化回傳表中不存在的條目 %r", term
            )
            return self._fallback(text, "無法對應到已知的症狀條目")

        if entry.is_too_broad:
            # 候選過多代表這個症狀本來就跨科（腹痛可以是內、外、婦、泌尿…），
            # 硬挑三個等於把不確定性藏起來。
            logger.info(
                f"{LOGGER_HEADER_TEXT} %r 候選 %d 個，超過上限，改走保底",
                term,
                len(entry.candidates),
            )
            return self._fallback(text, "這個症狀可能牽涉多個科別", matched_term=term)

        candidates = self._filter_pediatric(entry.candidates, text)
        return SymptomTriageResult(
            kind=RESULT_SUGGESTION,
            user_input=text,
            matched_term=term,
            candidates=candidates[:MAX_CANDIDATES],
        )

    def _filter_pediatric(
        self, candidates: tuple[DepartmentCandidate, ...], text: str
    ) -> tuple[DepartmentCandidate, ...]:
        """成人的提問不給兒科，除非兒科是唯一候選。"""
        if is_pediatric_age(get_request_age()) or mentions_child(text):
            return candidates
        without = tuple(c for c in candidates if c.canonical != PEDIATRIC_DEPARTMENT)
        # 全部濾光代表這個症狀只有兒科看，那就照實回傳——寧可給一個不完全
        # 適用的科別，也不要回一張空卡或無謂的保底。
        return without or candidates

    def _fallback(
        self, text: str, reason: str, *, matched_term: str | None = None
    ) -> SymptomTriageResult:
        candidates = tuple(
            DepartmentCandidate(
                canonical=name,
                subgroup=None,
                facility_count=0,
                source_count=0,
            )
            for name in FALLBACK_DEPARTMENTS
        )
        return SymptomTriageResult(
            kind=RESULT_FALLBACK,
            user_input=text,
            matched_term=matched_term,
            candidates=candidates,
            fallback_reason=reason,
        )
