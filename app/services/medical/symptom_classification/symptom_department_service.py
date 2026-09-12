"""
症狀 → 建議科別。流程：正規化 → 對照表 → 建議。

本服務只給「就診方向」，不做診斷：
    輸出一律是多候選（上限 MAX_CANDIDATES）加保底，並帶免責。原本
    department_matcher 明列「不做症狀分診」的 Non-Goal，理由是猜錯會把需要急診
    的人導向一般門診——那條理由仍然成立，但它現在由別的元件負責：急迫度判斷
    （urgency.py）擋在整個 agent 之前，判定為緊急的訊息根本不會走到這裡。改變的
    是問題設定：使用者本來就會問，答案要走一條有安全邊界的路，而不是落到沒有
    邊界的自由生成。

為什麼要過濾兒科：
    對照表有多條症狀同時掛在兒科與成人科別（腹痛、發燒、咳嗽…），因為那些
    症狀大人小孩都會有。不過濾時，成人問「我肚子好痛要掛哪一科」會拿到
    「內科、兒科」——兒科那一項對他沒有意義，卻佔掉一個候選名額。
    判斷依據是使用者填的年齡，加上訊息裡有沒有孩童指涉（家長幫小孩問時，
    年齡欄位是家長的）。兩者都不成立才濾掉；濾掉後沒有剩下任何候選時走保底，
    理由見 _filter_pediatric。

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
    SymptomEntry,
    SymptomTable,
)

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Services:SymptomTriage]"

# 保底建議（design 決策 6）。家醫科與內科的職責本就包含初步評估與轉診，把不確定
# 的人導到這裡是既有醫療體系的設計，不是系統在猜。不分科是未申報專科的一般西醫
# 診所，民眾最常就近去的地方，列出來是讓人知道「一般診所也可以」。
# 順序：家醫科在前，診所層級密度最高，也是卡片首選與「搜尋附近」按鈕的科別。
FALLBACK_DEPARTMENTS: tuple[str, ...] = ("家醫科", "內科", "不分科")

# 兒科的 canonical 值。過濾用，不寫死在方法裡以免與對照表脫鉤。
PEDIATRIC_DEPARTMENT = "兒科"

RESULT_SUGGESTION = "suggestion"
RESULT_FALLBACK = "fallback"


def _term_sources(entry: SymptomEntry) -> tuple[str, ...]:
    """對照表上找得到這個症狀的來源代碼，跨所有科別去重、保留出現順序。"""
    codes: dict[str, None] = {}
    for candidate in entry.candidates:
        for code in candidate.sources:
            codes.setdefault(code, None)
    return tuple(codes)

@dataclass(frozen=True)
class SymptomTriageResult:
    kind: str
    """RESULT_SUGGESTION / RESULT_FALLBACK"""

    user_input: str

    # --- 建議 ---
    matched_term: str | None = None
    candidates: tuple[DepartmentCandidate, ...] = field(default_factory=tuple)
    fallback_reason: str | None = None

    term_sources: tuple[str, ...] = field(default_factory=tuple)
    """對照表中收錄這個症狀的所有來源代碼，跨所有科別去重，含被兒科過濾掉的候選。

    卡片標註的分母 N 就是它的長度（design 決策 15）。與 candidates[].sources
    （分子 M）的差別在於粒度：後者是「這一家把這個症狀掛在這一科」，前者只是
    「這一家的表上找得到這個症狀」。沒收錄這個症狀的醫院不算反對，所以不進分母。
    """

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
            # 硬挑幾個等於把不確定性藏起來。
            logger.info(
                f"{LOGGER_HEADER_TEXT} %r 候選 %d 個，超過上限，改走保底",
                term,
                len(entry.candidates),
            )
            return self._fallback(text, "這個症狀可能牽涉多個科別", matched_term=term)

        candidates = self._filter_pediatric(entry.candidates, text)
        if not candidates:
            return self._fallback(
                text, "這個症狀在對照表中只列了兒科", matched_term=term
            )
        return SymptomTriageResult(
            kind=RESULT_SUGGESTION,
            user_input=text,
            matched_term=term,
            candidates=candidates[:MAX_CANDIDATES],
            term_sources=_term_sources(entry),
        )

    def _filter_pediatric(
        self, candidates: tuple[DepartmentCandidate, ...], text: str
    ) -> tuple[DepartmentCandidate, ...]:
        """成人的提問不給兒科。濾光時回傳空序列，由呼叫端走保底。"""
        if is_pediatric_age(get_request_age()) or mentions_child(text):
            return candidates
        without = tuple(c for c in candidates if c.canonical != PEDIATRIC_DEPARTMENT)
        if without:
            return without
        # 濾光有兩種可能，而程式分不出來：
        #   1. 這個症狀真的只有兒科看（尿床、生長發育遲緩、新生兒照護）
        #   2. 表缺了成人科別（「嘔吐」只有榮總玉里在小兒科總表裡收錄，成大與
        #      台大未單列）
        # 初版在這裡回傳兒科，於是第 2 種情況會給成人一個明確錯誤的答案。
        # 改成走保底：第 1 種情況成人本來就不會問，就算問了「家醫科」也比
        # 「兒科」合理；第 2 種情況則從「錯的答案」降級為「誠實的不確定」。
        return ()

    def _fallback(
        self, text: str, reason: str, *, matched_term: str | None = None
    ) -> SymptomTriageResult:
        candidates = tuple(
            DepartmentCandidate(
                canonical=name,
                subgroup=None,
                facility_count=0,
                sources=(),
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
