import logging

from app.services.gemini import GeminiResult, GeminiService
from app.services.guardrail import GuardrailService
from app.services.RAG.retrieval import RagNoHitsError
from app.services.RAG.services import RagAnswerService
from app.tools.registry import get_all_gemini_tools

logger = logging.getLogger(__name__)


class ResponseOrchestrator:
    def __init__(
        self,
        gemini_service: GeminiService,
        guardrail_service: GuardrailService,
        rag_answer_service: RagAnswerService,
    ) -> None:
        self.gemini_service = gemini_service
        self.guardrail_service = guardrail_service
        self.rag_answer_service = rag_answer_service

    async def orchestrate_response(self, user_text: str) -> GeminiResult:
        allow_rag_tool = await self.guardrail_service.allow_rag_tool(user_text)
        tools = get_all_gemini_tools(include_rag_tool=allow_rag_tool)

        logger.info(f"allow_rag_tool={allow_rag_tool}, tools count={len(tools)}")

        result = await self.gemini_service.generate_response(
            user_text, tools=tools,
        )

        logger.info(
            f"Gemini 回傳: is_function_call={result.is_function_call}, "
            f"function_name={result.function_name}, "
            f"has_text={result.text is not None}"
        )

        if not result.is_function_call:
            return result

        if result.function_name != "get_rag_answer":
            return result

        query = str(result.function_args.get("query") or user_text).strip()
        try:
            rag_text = await self.rag_answer_service.answer(query)
            return GeminiResult(text=rag_text)
        except RagNoHitsError:
            logger.warning("RAG 無命中，重試 Gemini（不含 RAG tool）")
            return await self._retry_without_rag(user_text)
        except Exception as e:
            logger.error(
                f"RAG tool 執行失敗，重試 Gemini（不含 RAG tool）: {e}",
                exc_info=True,
            )
            return await self._retry_without_rag(user_text)

    async def _retry_without_rag(self, user_text: str) -> GeminiResult:
        """RAG 失敗後的重試：排除 RAG tool 避免再次觸發，但保留其他 tools。"""
        tools = get_all_gemini_tools(include_rag_tool=False)
        result = await self.gemini_service.generate_response(
            user_text, tools=tools,
        )

        logger.info(
            f"重試結果: is_function_call={result.is_function_call}, "
            f"function_name={result.function_name}"
        )

        if result.is_function_call:
            return result

        return result
