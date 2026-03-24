from app.tools.medical_tools import medical_function_declarations
from app.tools.rag_tools import rag_function_declarations

def get_all_gemini_tools(include_rag_tool: bool = True) -> list:

    all_declarations = []
    
    # 加入醫療相關工具
    all_declarations.extend(medical_function_declarations)
    if include_rag_tool:
        all_declarations.extend(rag_function_declarations)
    
    # 未來若有其他模組的工具，也可以持續 .extend()
    
    if not all_declarations:
        return []
        
    return [
        {
            "functionDeclarations": all_declarations
        }
    ]
