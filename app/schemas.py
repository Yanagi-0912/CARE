from pydantic import BaseModel, Field
#pydantic 是來做資料驗證的還有資料管理的，比一般的python class 好一點的是為自動檢查是否符合規則
#EX json 傳回來的是字串，像是有一欄是age就要把json的字串轉成int
#有了pydantic 會先建立一個basemodel 的基本model
class AIRequest(BaseModel):
    """AI 回應請求模型"""
    user_input: str = Field(
        ..., #這個代表必填欄位，如果沒有傳就會報422錯誤
        description="使用者輸入的問題或訊息",#api文件
        json_schema_extra={"example": "請告詞我台北市有哪些醫院？"}
    )

class AIResponse(BaseModel):
    """AI 回應成功模型"""
    response: str = Field(
        ..., 
        description="AI 生成的回應內容",
        json_schema_extra={"example": "台北市有許多醫院，包括台大醫院、榮民總醫院等..."}
    )

class ErrorResponse(BaseModel):
    """錯誤回應模型"""
    error: str = Field(
        ..., 
        description="錯誤訊息",
        json_schema_extra={"example": "user_input is required"}
    )

class HealthResponse(BaseModel):
    """健康檢查回應模型"""
    status: str = Field(
        ..., 
        description="服務狀態訊息",
        json_schema_extra={"example": "Welcome to CARE Backend!"}
    )

class RootResponse(BaseModel):
    """根路徑回應模型"""
    message: str = Field(
        ..., 
        description="歡迎訊息",
        json_schema_extra={"example": "CARE Backend Running"}
    )
