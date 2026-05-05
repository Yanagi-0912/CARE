from app.tools.medical_tools import medical_function_declarations
from app.tools.rag_tools import rag_function_declarations


def get_all_gemini_tools(include_rag_tool: bool = True) -> list[dict]:
    """回傳 LangChain `bind_tools` 可用的扁平函式宣告列表。

    舊：回傳 Google generateContent 格式 `[{"functionDeclarations": [...]}]`。
    新：改成扁平 `list[dict]`，每個元素含 name / description / parameters，
        直接給 `ChatGoogleGenerativeAI.bind_tools(...)` 使用。
    """
    all_declarations: list[dict] = []

    all_declarations.extend(medical_function_declarations)
    if include_rag_tool:
        all_declarations.extend(rag_function_declarations)

    return all_declarations
