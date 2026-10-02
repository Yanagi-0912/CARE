"""RagAnswerService 的複合問題拆題路徑（openspec: rag-compound-questions）。"""

from unittest.mock import AsyncMock, MagicMock

from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from app.core.user_language import reset_request_language, set_request_language
from app.i18n.messages import t
from app.services.rag import RagAnswerService
from app.services.rag.cannot_answer import NO_ANSWER_SENTINEL
from app.services.rag.cohere_reranker import VectorScoreReranker
from app.services.rag.fail_messages import is_rag_fail
from app.services.rag.question_decomposer import SubQuestion
from app.services.rag.retrieval_grader import Grade

MESSAGE = "我三酸甘油脂偏高，魚油跟魚肝油有什麼差別？正在吃降血脂藥還要注意什麼？"
AGENT_QUERY = "魚油 魚肝油 差別 降血脂藥"
SUB_A = SubQuestion("魚油與魚肝油有什麼差別？", "魚油與魚肝油的差別")
SUB_B = SubQuestion("正在吃降血脂藥的人補充魚油要注意什麼？", "降血脂藥與魚油併用的注意事項")


def _doc(url: str, content: str, score: float = 0.9) -> Document:
    return Document(
        page_content=content,
        metadata={"source_name": "國健署", "url": url, "original_title": content[:8], "score": score},
    )


DOCS_A = [_doc("https://www.hpa.gov.tw/a1", "魚油含 EPA 與 DHA"), _doc("https://www.hpa.gov.tw/a2", "魚肝油含維生素 A 與 D", 0.8)]
DOCS_B = [_doc("https://www.fda.gov.tw/b1", "降血脂藥併用魚油可能增加出血風險")]
DOCS_ORIGINAL = [_doc("https://www.hpa.gov.tw/o1", "高血脂的飲食原則")]


class _Retriever:
    def __init__(self, by_query: dict[str, list[Document]]) -> None:
        self.by_query = by_query
        self.queries: list[str] = []

    async def ainvoke(self, query: str) -> list[Document]:
        self.queries.append(query)
        return list(self.by_query.get(query, []))


class _Grader:
    def __init__(self, by_question: dict[str, Grade]) -> None:
        self.by_question = by_question
        self.questions: list[str] = []

    async def grade(self, query: str, docs: list[Document]) -> Grade:
        self.questions.append(query)
        return self.by_question.get(query, Grade.INCORRECT)


class _BoomGrader:
    async def grade(self, query: str, docs: list[Document]) -> Grade:
        raise RuntimeError("grader down")


class _Decomposer:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result if result is not None else [SUB_A, SUB_B]
        self.error = error
        self.messages: list[str] = []

    async def decompose(self, message: str) -> list[SubQuestion]:
        self.messages.append(message)
        if self.error is not None:
            raise self.error
        return list(self.result)


def _service(*, decomposer, grader, answer="關於魚油：魚油含 EPA [1]。", retriever=None):
    gemini = MagicMock()
    gemini.chat_model.ainvoke = AsyncMock(return_value=AIMessage(content=answer))
    retriever = retriever or _Retriever(
        {SUB_A.retrieval_query: DOCS_A, SUB_B.retrieval_query: DOCS_B, AGENT_QUERY: DOCS_ORIGINAL}
    )
    service = RagAnswerService(
        gemini_service=gemini,
        retriever=retriever,
        reranker=VectorScoreReranker(),
        grader=grader,
        crag_enabled=True,
        speculative_generate=False,
        web_fallback_enabled=False,
        decomposer=decomposer,
    )
    return service, gemini, retriever


def _prompt_text(gemini) -> str:
    return "\n".join(m.content for m in gemini.chat_model.ainvoke.await_args.args[0])


async def test_without_original_message_does_not_decompose():
    decomposer = _Decomposer()
    service, _, retriever = _service(decomposer=decomposer, grader=_Grader({AGENT_QUERY: Grade.CORRECT}))

    await service.answer(AGENT_QUERY)

    assert decomposer.messages == []
    assert retriever.queries == [AGENT_QUERY]


async def test_single_question_mark_does_not_decompose():
    decomposer = _Decomposer()
    service, _, _ = _service(decomposer=decomposer, grader=_Grader({AGENT_QUERY: Grade.CORRECT}))

    await service.answer(AGENT_QUERY, original_message="魚油跟魚肝油有什麼差別？")

    assert decomposer.messages == []


async def test_non_zh_tw_request_does_not_decompose():
    decomposer = _Decomposer()
    service, _, _ = _service(decomposer=decomposer, grader=_Grader({AGENT_QUERY: Grade.CORRECT}))
    token = set_request_language("en")
    try:
        await service.answer(AGENT_QUERY, original_message="Fish oil vs cod liver oil? Any drug interactions?")
    finally:
        reset_request_language(token)

    assert decomposer.messages == []


async def test_decomposer_not_injected_keeps_current_path():
    service, _, retriever = _service(decomposer=None, grader=_Grader({AGENT_QUERY: Grade.CORRECT}))

    await service.answer(AGENT_QUERY, original_message=MESSAGE)

    assert retriever.queries == [AGENT_QUERY]


async def test_single_decomposition_falls_back_to_agent_query():
    decomposer = _Decomposer(result=[])
    service, _, retriever = _service(decomposer=decomposer, grader=_Grader({AGENT_QUERY: Grade.CORRECT}))

    await service.answer(AGENT_QUERY, original_message=MESSAGE)

    assert decomposer.messages == [MESSAGE]
    assert retriever.queries == [AGENT_QUERY]


async def test_all_supported_retrieves_per_sub_and_grades_with_question():
    grader = _Grader({SUB_A.question: Grade.CORRECT, SUB_B.question: Grade.CORRECT})
    service, gemini, retriever = _service(
        decomposer=_Decomposer(), grader=grader,
        answer="關於差別：魚油含 EPA [1]。\n\n關於用藥：可能增加出血風險 [2]。",
    )

    out = await service.answer(AGENT_QUERY, original_message=MESSAGE)

    # 子問題用各自的檢索寫法，不以原句或 agent query 檢索
    assert sorted(retriever.queries) == sorted([SUB_A.retrieval_query, SUB_B.retrieval_query])
    # 分級用回答用的問題
    assert sorted(grader.questions) == sorted([SUB_A.question, SUB_B.question])
    prompt = _prompt_text(gemini)
    assert SUB_A.question in prompt and SUB_B.question in prompt
    assert MESSAGE not in prompt
    assert not is_rag_fail(out)
    assert "參考資料來源" in out
    assert "目前知識庫沒有找到可靠資料" not in out


async def test_partially_supported_adds_uncited_note_for_unsupported_sub():
    grader = _Grader({SUB_A.question: Grade.CORRECT, SUB_B.question: Grade.AMBIGUOUS})
    service, gemini, _ = _service(decomposer=_Decomposer(), grader=grader)

    out = await service.answer(AGENT_QUERY, original_message=MESSAGE)

    prompt = _prompt_text(gemini)
    assert SUB_A.question in prompt
    assert SUB_B.question not in prompt
    note = t("rag.compound_unsupported", "zh-TW").format(question=SUB_B.question)
    assert note in out
    note_line = next(line for line in out.splitlines() if SUB_B.question in line)
    assert "[" not in note_line
    # 生成只看得到通過子問題的文件
    assert "出血風險" not in prompt


async def test_evidence_interleaves_supported_subs_within_top_n():
    many_a = [_doc(f"https://www.hpa.gov.tw/a{i}", f"魚油資料{i}", 0.9 - i * 0.01) for i in range(6)]
    many_b = [_doc(f"https://www.fda.gov.tw/b{i}", f"用藥資料{i}", 0.5 - i * 0.01) for i in range(6)]
    retriever = _Retriever({SUB_A.retrieval_query: many_a, SUB_B.retrieval_query: many_b})
    grader = _Grader({SUB_A.question: Grade.CORRECT, SUB_B.question: Grade.CORRECT})
    service, gemini, _ = _service(decomposer=_Decomposer(), grader=grader, retriever=retriever)

    await service.answer(AGENT_QUERY, original_message=MESSAGE)

    prompt = _prompt_text(gemini)
    # 分數較低的子問題 B 也分得到名額，不會被 A 的文件擠光
    assert "用藥資料0" in prompt
    assert sum(f"魚油資料{i}" in prompt for i in range(6)) + sum(f"用藥資料{i}" in prompt for i in range(6)) == service.rerank_top_n


async def test_none_supported_falls_back_to_agent_query():
    grader = _Grader({AGENT_QUERY: Grade.CORRECT})
    service, _, retriever = _service(decomposer=_Decomposer(), grader=grader)

    out = await service.answer(AGENT_QUERY, original_message=MESSAGE)

    assert retriever.queries[-1] == AGENT_QUERY
    assert not is_rag_fail(out)


async def test_decomposer_error_falls_back_to_agent_query():
    decomposer = _Decomposer(error=RuntimeError("quota"))
    service, _, retriever = _service(decomposer=decomposer, grader=_Grader({AGENT_QUERY: Grade.CORRECT}))

    out = await service.answer(AGENT_QUERY, original_message=MESSAGE)

    assert retriever.queries == [AGENT_QUERY]
    assert not is_rag_fail(out)


async def test_grader_error_falls_back_to_current_path():
    service, _, retriever = _service(decomposer=_Decomposer(), grader=_BoomGrader())

    await service.answer(AGENT_QUERY, original_message=MESSAGE)

    assert retriever.queries[-1] == AGENT_QUERY


async def test_sentinel_with_citations_is_stripped():
    grader = _Grader({SUB_A.question: Grade.CORRECT, SUB_B.question: Grade.CORRECT})
    service, _, _ = _service(
        decomposer=_Decomposer(), grader=grader,
        answer=f"{NO_ANSWER_SENTINEL}\n關於差別：魚油含 EPA [1]。",
    )

    out = await service.answer(AGENT_QUERY, original_message=MESSAGE)

    assert NO_ANSWER_SENTINEL not in out
    assert not is_rag_fail(out)
    assert "魚油含 EPA" in out


async def test_sentinel_only_falls_back_to_current_path():
    grader = _Grader({SUB_A.question: Grade.CORRECT, SUB_B.question: Grade.CORRECT, AGENT_QUERY: Grade.INCORRECT})
    service, _, retriever = _service(
        decomposer=_Decomposer(), grader=grader, answer=f"{NO_ANSWER_SENTINEL}\n找不到相關資料。",
    )

    out = await service.answer(AGENT_QUERY, original_message=MESSAGE)

    assert retriever.queries[-1] == AGENT_QUERY
    assert is_rag_fail(out)


async def test_compound_path_is_reported_in_timing_logs(caplog):
    grader = _Grader({SUB_A.question: Grade.CORRECT, SUB_B.question: Grade.CORRECT})
    service, _, _ = _service(decomposer=_Decomposer(), grader=grader)

    with caplog.at_level("INFO"):
        await service.answer(AGENT_QUERY, original_message=MESSAGE)

    text = caplog.text
    assert "path=kb_compound" in text
    assert "rag_compound subs=2 supported=2" in text
    # 日誌不含使用者原文
    assert MESSAGE not in text
