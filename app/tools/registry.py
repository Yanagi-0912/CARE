from app.tools.medical_tools import medical_function_declarations

def get_all_gemini_tools() -> list:

    all_declarations = []
    
    # 加入醫療相關工具
    all_declarations.extend(medical_function_declarations)
    
    # 未來若有其他模組的工具，也可以持續 .extend()
    
    if not all_declarations:
        return []
        
    return [
        {
            "functionDeclarations": all_declarations
        }
    ]
