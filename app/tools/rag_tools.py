from langchain_core.tools import tool


@tool
def get_rag_answer(query: str) -> str:
    """
    當問題需要引用內部醫療知識庫內容時呼叫。
    例如疾病照護建議、症狀處置原則、慢病管理等。
    """
    pass


rag_function_declarations = [
    {
        "name": "get_rag_answer",
        "description": (
            "當問題需要引用內部醫療知識庫內容時呼叫。"
            "例如疾病照護建議、症狀處置原則、慢病管理等。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要拿去檢索知識庫的問題文字。",
                }
            },
            "required": ["query"],
        },
    }
]
