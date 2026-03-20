from app.tools.medical_tools import medical_function_declarations

def get_all_gemini_tools() -> list:
    """
    彙整全域所有的 Gemini 工具宣告，回傳格式須符合 Gemini API 要求
    如此一來，新增網域邏輯的工具（如預約、查詢資料等）都只需要在此引入合併即可。
    """
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
