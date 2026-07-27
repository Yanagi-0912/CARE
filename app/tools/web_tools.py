from langchain_core.tools import tool

_web_search_service = None


def configure_web_tool(web_search_service) -> None:
    """DI 初始化時呼叫，注入 WebSearchService 實例。"""
    global _web_search_service
    _web_search_service = web_search_service


@tool
async def search_public_web(query: str) -> str:
    """當知識庫（get_rag_answer）沒有足夠資料，或需要查詢政府公開網頁補充時呼叫。
    會搜尋允許的公開網站並整理成回答。優先使用 get_rag_answer；若其回覆表示找不到／不知道，再呼叫本工具。
    """
    if _web_search_service is None:
        return "網路搜尋服務未初始化，請稍後再試。"
    return await _web_search_service.answer(query)
