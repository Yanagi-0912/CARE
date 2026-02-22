from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_ai_response_endpoint():
    """測試正常的 AI 回應"""
    response = client.post(
        "/api/v1/ai_response",
        json={"user_input":"你好"}
    )

    assert response.status_code == 200 
    assert "response" in response.json()
    
    # 輸出實際的 AI 回應內容
    result = response.json()
    print("\n" + "="*50)
    print("AI 回應測試結果:")
    print("="*50)
    print(f"輸入: 你好")
    if "response" in result:
        print(f"輸出: {result['response']}")
    else:
        print(f"錯誤: {result.get('error', '未知錯誤')}")
    print("="*50 + "\n")

def test_ai_response_missing_user_input():
    """測試缺少 user_input 參數"""
    response = client.post(
        "/api/v1/ai_response",
        json={}
    )
    
    # Pydantic 模型驗證會返回 422 Unprocessable Entity
    assert response.status_code == 422
    assert "detail" in response.json()

def test_ai_response_empty_user_input():
    """測試 user_input 為空字串"""
    response = client.post(
        "/api/v1/ai_response",
        json={"user_input": ""}
    )
    
    # 空字串會通過 Pydantic 驗證，但被我們的邏輯捕獲
    assert response.status_code == 200
    assert response.json() == {"error": "user_input is required"}

def test_ai_response_wrong_method():
    """測試使用錯誤的 HTTP 方法（GET）"""
    response = client.get("/api/v1/ai_response")
    
    assert response.status_code == 405  # Method Not Allowed