"""複合問題的偵測與拆題（見 openspec/changes/compound-question-decomposition）。

長輩常在一則訊息裡問兩件事。現行 RAG 對整題只分一次級，只要其中一個子問題
查不到，整題就回「知識庫無資料」，另一半答得出來的也一起丟掉。這裡負責兩件事：

1. `looks_compound`：便宜的規則閘，問號 ≥ 2 才值得花一次 LLM 呼叫去拆。
   2026-09-29 實驗 C：只用拆題器時會把「飲食和活動要注意什麼」拆成兩題
   （單題過度拆 3/48）；加上這道閘之後單題與控制題 0 誤判、複合題命中 21/24。
2. `GeminiQuestionDecomposer`：一次 LLM 呼叫，判斷是否為多個需分別查資料的
   獨立問題，並為每個子問題寫出兩種寫法：
   - `question`：回答與 CRAG 分級用，只保留「拿掉會改變答案」的條件
   - `retrieval_query`：檢索與精排用，只留核心主題

為什麼要兩種寫法、為什麼要刪條件——都是量出來的（2026-09-29～30 實驗 C2／C3）：
拆題器若把原題條件全部帶進每個子問題（例如「同時有糖尿病和腎臟病」進了兩題），
每題都變成知識庫沒有的多條件查詢；只留會改變答案的條件，閘門內複合題答出
3.3→4.3/8。檢索用核心主題後精排明顯變好（T06 Q1 0.63→0.86），分級仍用帶條件的
`question`，判的是「這些文件能不能回答這位使用者」——那是刻意保留的安全行為。

prompt 文字與實驗 C3 的 v3_split 版本相同；改動請重跑
`CARE_compound_medical_pilot_2026` 題組比對。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import HumanMessage

from app.services.gemini import GeminiService
from app.services.gemini.shared.parser import content_to_text, parse_json_from_model_text

# 問號 ≥ 2 才嘗試拆題。只數問號是刻意的：題組裡唯一漏掉的 T08 用「；」連接兩問，
# 但把分號也算進來會讓一般的長句被誤送去拆題，要等真實 LINE 訊息量過再放寬。
MIN_QUESTION_MARKS = 2
# 子問題上限。實驗中拆題器最多拆出 3 題；再多的話每題都要各自檢索、精排、分級。
MAX_SUB_QUESTIONS = 3

_PROMPT = """你是健康問答系統的問題分析器。判斷使用者這則訊息是「一個問題」還是「多個需要分別查資料才能回答的獨立問題」，需要時拆成子問題。

規則：
1. 只有當訊息包含兩個以上、各自需要查不同資料才能回答的獨立問題時，strategy 才是 "compound"，列出 2 到 3 個子問題。
2. 同一個問題帶有多個條件或病況（例如「同時有糖尿病和腎臟病的長輩能不能吃高蛋白」）只是一個問題，strategy 是 "single"。
3. 子問題用自然、完整的問句，不要寫成關鍵字串，不要加入使用者沒提到的內容；每個子問題都要能單獨看懂。
4. 情境：每個子問題只保留「會改變這個子問題答案」的情境。逐一檢查原訊息中的每個情境或條件，問自己：「拿掉它，這個子問題的正確答案會不會不同？」會不同才保留（例如正在服用的藥物、會影響建議內容的疾病或身分）；不會不同就刪掉（例如購買經過、誤會的由來、網路傳言的出處、與這個子問題無關的病況）。kept_context 列出保留的情境，dropped_context 列出刪掉的情境。
5. 另外為每個子問題寫一個 retrieval_query：用來在衛教知識庫搜尋文章的一句自然問句。只保留決定「要找哪一類衛教文章」的核心主題；使用者個人的病況、用藥、年齡等條件，除非少了它就找不到對的文章（例如問題本身就是某種藥物的交互作用），否則不要放進 retrieval_query（這些條件仍保留在 question 裡）。不要寫成關鍵字串。
6. 只輸出 JSON，不要其他文字：
{{"strategy": "single" 或 "compound", "sub_questions": [{{"question": "...", "retrieval_query": "...", "kept_context": ["..."], "dropped_context": ["..."]}}]}}
（single 時 sub_questions 為空陣列）

使用者訊息：{message}"""


@dataclass(frozen=True)
class SubQuestion:
    question: str
    retrieval_query: str


class QuestionDecomposer(Protocol):
    async def decompose(self, message: str) -> list[SubQuestion]:
        """回傳子問題；不是複合問題（或少於 2 題）時回空 list。失敗時拋例外。"""
        ...


def looks_compound(text: str | None) -> bool:
    """問號（全形或半形）≥ 2 才值得花一次 LLM 呼叫去拆。"""
    if not text:
        return False
    return text.count("？") + text.count("?") >= MIN_QUESTION_MARKS


def parse_decomposition(raw_text: str) -> list[SubQuestion]:
    """解析拆題器輸出；不是 compound 或有效子問題少於 2 題時回空 list。

    JSON 本身壞掉時讓例外往上拋，由呼叫端退回現行流程。
    """
    data = parse_json_from_model_text(raw_text)
    if data.get("strategy") != "compound":
        return []
    subs: list[SubQuestion] = []
    for item in data.get("sub_questions") or []:
        if not isinstance(item, dict):
            continue
        question = item.get("question")
        if not isinstance(question, str) or not question.strip():
            continue
        query = item.get("retrieval_query")
        question = question.strip()
        subs.append(
            SubQuestion(
                question=question,
                retrieval_query=query.strip() if isinstance(query, str) and query.strip() else question,
            )
        )
    subs = subs[:MAX_SUB_QUESTIONS]
    return subs if len(subs) >= 2 else []


class GeminiQuestionDecomposer:
    def __init__(self, gemini_service: GeminiService) -> None:
        self._gemini_service = gemini_service

    async def decompose(self, message: str) -> list[SubQuestion]:
        result = await self._gemini_service.chat_model.ainvoke(
            [HumanMessage(content=_PROMPT.format(message=message))]
        )
        return parse_decomposition(content_to_text(result.content))
