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
    判斷依據是 PatientContext 中實際看診者的年齡，加上訊息裡是否明確提到「寶寶」。
    兩者都不成立才濾掉；「兒子／女兒／child」只代表關係，不代表未成年。濾掉後
    沒有剩下任何候選時走保底，理由見 _filter_pediatric。

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
import re
from dataclasses import dataclass, field

from app.core.user_age import is_pediatric_age
from app.services.family.patient_context import PatientContext
from app.services.medical.department_matcher import resolve_department
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
# 順序：家醫科在前，診所層級密度最高，也是卡片標題列的首選；「搜尋附近」按鈕則
# 一次搜尋這裡列出的全部科別。為孩童詢問時，另在最前面多列兒科（見 _fallback）。
FALLBACK_DEPARTMENTS: tuple[str, ...] = ("家醫科", "內科", "不分科")

# 兒科的 canonical 值。過濾用，不寫死在方法裡以免與對照表脫鉤。
PEDIATRIC_DEPARTMENT = "兒科"
OBSTETRICS_GYNECOLOGY_DEPARTMENT = "婦產科"

# 本輪明確語意高於 profile 性別。只處理懷孕、生產、月經與生殖脈絡，不把
# 「腹痛」「性病」等跨性別的一般症狀算進來。外語詞讓六語工具契約不必先翻中文；
# matched_term 也一起檢查，避免正規化後的專屬症狀失去語意。
# 不收「生產」（生產線）與日文單字「生理」（生理時鐘）：一般用語也會用到，男性
# 講到就會把婦產科列回來。拉丁字母的詞要整個字相符，見 _REPRODUCTIVE_WORD_PATTERN。
_REPRODUCTIVE_CONTEXT_TERMS = (
    "懷孕", "妊娠", "孕婦", "孕期", "生小孩", "臨盆", "待產", "分娩",
    "宮縮", "羊水", "胎兒", "胎動", "產前", "產後", "流產", "月經", "經期",
    "經痛", "生理期", "停經", "生殖", "不孕", "避孕", "子宮", "卵巢", "陰道", "婦科",
    "pregnant", "pregnancy", "childbirth", "giving birth", "in labor", "menstrual",
    "menstruation", "reproductive", "infertility", "contraception", "uterus", "ovary", "vaginal",
    "hamil", "kehamilan", "melahirkan", "menstruasi", "haid", "reproduksi", "rahim",
    "ovarium", "vagina", "kontrasepsi", "infertil",
    "mang thai", "thai kỳ", "sinh con", "kinh nguyệt", "hành kinh", "sinh sản", "tử cung",
    "buồng trứng", "âm đạo", "tránh thai", "vô sinh",
    "ตั้งครรภ์", "คลอด", "ประจำเดือน", "สืบพันธุ์", "มดลูก", "รังไข่", "ช่องคลอด",
    "คุมกำเนิด", "มีบุตรยาก",
    "妊娠", "出産", "月経", "生理痛", "生理中", "生理不順", "不妊", "避妊", "生殖", "子宮", "卵巣", "膣",
)

# 保底多列兒科的原因。卡片依原因用不同說法：提到孩童是家長在問，年齡未滿界線則是
# 孩童本人在問。
PEDIATRIC_REASON_MENTIONED_CHILD = "mentioned_child"
PEDIATRIC_REASON_AGE = "age"

RESULT_SUGGESTION = "suggestion"
RESULT_FALLBACK = "fallback"


def _term_sources(entry: SymptomEntry) -> tuple[str, ...]:
    """對照表上找得到這個症狀的來源代碼，跨所有科別去重、保留出現順序。"""
    codes: dict[str, None] = {}
    for candidate in entry.candidates:
        for code in candidate.sources:
            codes.setdefault(code, None)
    return tuple(codes)


def _pediatric_reason(
    text: str,
    patient_context: PatientContext | None,
) -> str | None:
    """
    這次是不是為孩童詢問；是的話回傳原因（PEDIATRIC_REASON_*），否則 None。

    訊息提到寶寶優先於年齡，卡片要用「幫孩子詢問」的說法。除此之外只讀
    PatientContext.age；發話者的 request ContextVar 與家人稱謂都不得影響結果。
    """
    if mentions_child(text):
        return PEDIATRIC_REASON_MENTIONED_CHILD
    age = patient_context.age if patient_context is not None else None
    if (
        isinstance(age, int)
        and not isinstance(age, bool)
        and 0 <= age <= 130
        and is_pediatric_age(age)
    ):
        return PEDIATRIC_REASON_AGE
    return None


# 拉丁字母的詞（英文、印尼文、越南文）逐字比對會誤中別的字：hamil 在 Hamilton
# 裡、haid 在 haida 裡。這些詞只在整個字相符時算數；中日泰文沒有空白分詞，照舊
# 用包含比對。
_REPRODUCTIVE_WORD_PATTERN = re.compile(
    "|".join(
        rf"\b{re.escape(term.casefold())}\b"
        for term in _REPRODUCTIVE_CONTEXT_TERMS
        if term.isascii() or re.search(r"[a-z]", term)
    )
)
_REPRODUCTIVE_SUBSTRING_TERMS = tuple(
    term.casefold()
    for term in _REPRODUCTIVE_CONTEXT_TERMS
    if not (term.isascii() or re.search(r"[a-z]", term))
)


def _has_reproductive_context(text: str, matched_term: str) -> bool:
    normalized = f"{text} {matched_term}".casefold()
    return bool(_REPRODUCTIVE_WORD_PATTERN.search(normalized)) or any(
        term in normalized for term in _REPRODUCTIVE_SUBSTRING_TERMS
    )


def _explicitly_requests_obstetrics(requested_department: str) -> bool:
    match = resolve_department(requested_department)
    return bool(match and match.canonical == OBSTETRICS_GYNECOLOGY_DEPARTMENT)


def _should_include_obstetrics(
    text: str,
    matched_term: str | None,
    requested_department: str,
) -> bool:
    return _has_reproductive_context(text, matched_term or "") or (
        _explicitly_requests_obstetrics(requested_department)
    )


@dataclass(frozen=True)
class SymptomTriageResult:
    kind: str
    """RESULT_SUGGESTION / RESULT_FALLBACK"""

    user_input: str
    patient_context: PatientContext | None = None
    """本次症狀所屬的看診者；10.7 起由流程依解析結果決定後續處置。"""

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

    pediatric_reason: str | None = None
    """保底多列了兒科的原因（PEDIATRIC_REASON_*）；非孩童詢問、或不是保底時為 None。"""

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

    async def suggest(
        self,
        text: str,
        *,
        patient_context: PatientContext | None = None,
        requested_department: str = "",
    ) -> SymptomTriageResult:
        term = await self._normalizer.resolve(text)
        if term is None:
            return self._fallback(
                text,
                "無法對應到已知的症狀條目",
                patient_context=patient_context,
                requested_department=requested_department,
            )

        entry = self._table.lookup(term)
        if entry is None:
            # 正規化層的 enum 應該擋掉這種情況，走到這裡代表表與正規化層不同步。
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 正規化回傳表中不存在的條目 %r", term
            )
            return self._fallback(
                text,
                "無法對應到已知的症狀條目",
                patient_context=patient_context,
                requested_department=requested_department,
            )

        if entry.is_too_broad:
            # 候選過多代表這個症狀本來就跨科（腹痛可以是內、外、婦、泌尿…），
            # 硬挑幾個等於把不確定性藏起來。
            logger.info(
                f"{LOGGER_HEADER_TEXT} %r 候選 %d 個，超過上限，改走保底",
                term,
                len(entry.candidates),
            )
            return self._fallback(
                text,
                "這個症狀可能牽涉多個科別",
                matched_term=term,
                patient_context=patient_context,
                requested_department=requested_department,
            )

        candidates = self._filter_gender_applicability(
            entry.candidates,
            text=text,
            matched_term=term,
            patient_context=patient_context,
            requested_department=requested_department,
        )
        if not candidates:
            return self._fallback(
                text,
                "依看診者資料，沒有適合預設顯示的特定科別",
                matched_term=term,
                patient_context=patient_context,
                requested_department=requested_department,
            )

        candidates = self._filter_pediatric(
            candidates,
            text,
            patient_context,
        )
        if not candidates:
            return self._fallback(
                text,
                "這個症狀在對照表中只列了兒科",
                matched_term=term,
                patient_context=patient_context,
                requested_department=requested_department,
            )
        return SymptomTriageResult(
            kind=RESULT_SUGGESTION,
            user_input=text,
            patient_context=patient_context,
            matched_term=term,
            candidates=candidates[:MAX_CANDIDATES],
            term_sources=_term_sources(entry),
        )

    @staticmethod
    def _filter_gender_applicability(
        candidates: tuple[DepartmentCandidate, ...],
        *,
        text: str,
        matched_term: str,
        patient_context: PatientContext | None,
        requested_department: str,
    ) -> tuple[DepartmentCandidate, ...]:
        """男性的一般症狀不預設顯示婦產科；明確語意與指定科別優先。"""
        if patient_context is None or patient_context.gender != "male":
            return candidates
        if _should_include_obstetrics(text, matched_term, requested_department):
            return candidates
        return tuple(
            candidate
            for candidate in candidates
            if candidate.canonical != OBSTETRICS_GYNECOLOGY_DEPARTMENT
        )

    def _filter_pediatric(
        self,
        candidates: tuple[DepartmentCandidate, ...],
        text: str,
        patient_context: PatientContext | None,
    ) -> tuple[DepartmentCandidate, ...]:
        """成人的提問不給兒科。濾光時回傳空序列，由呼叫端走保底。"""
        if _pediatric_reason(text, patient_context) is not None:
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
        self,
        text: str,
        reason: str,
        *,
        matched_term: str | None = None,
        patient_context: PatientContext | None = None,
        requested_department: str = "",
    ) -> SymptomTriageResult:
        # 明確生殖情境／指定婦產科與孩童提示可在一般初診方向前補上對應科別。
        pediatric_reason = _pediatric_reason(text, patient_context)
        prefixes: list[str] = []
        if _should_include_obstetrics(text, matched_term, requested_department):
            prefixes.append(OBSTETRICS_GYNECOLOGY_DEPARTMENT)
        if pediatric_reason is not None:
            prefixes.append(PEDIATRIC_DEPARTMENT)
        names = tuple(dict.fromkeys((*prefixes, *FALLBACK_DEPARTMENTS)))
        candidates = tuple(
            DepartmentCandidate(
                canonical=name,
                subgroup=None,
                facility_count=0,
                sources=(),
            )
            for name in names
        )
        return SymptomTriageResult(
            kind=RESULT_FALLBACK,
            user_input=text,
            patient_context=patient_context,
            matched_term=matched_term,
            candidates=candidates,
            fallback_reason=reason,
            pediatric_reason=pediatric_reason,
        )
