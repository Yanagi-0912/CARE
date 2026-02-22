from pydantic import BaseModel, Field

class AIRequest(BaseModel):
    """AI 回應請求模型"""
    user_input: str = Field(
        ..., 
        description="使用者輸入的問題或訊息",
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
