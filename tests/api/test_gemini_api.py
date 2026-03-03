from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_ai_response_endpoint():
    response = client.post(
        "/api/v1/ai_response",
        json={"user_input": "你好"},
    )
    assert response.status_code == 200
    assert "response" in response.json()


def test_ai_response_missing_user_input():#測 格式error 是不是會格式錯誤代碼422
    response = client.post("/api/v1/ai_response", json={})
    assert response.status_code == 422 #422 是fastapi的錯誤碼，表示請求格式錯誤EX body 沒有user_input
    assert "detail" in response.json()


def test_ai_response_empty_user_input():#當沒有發生422 錯誤碼， body 裡確實有user_input 但是輸出是空字串
    response = client.post(
        "/api/v1/ai_response",
        json={"user_input": ""},
    )
    assert response.status_code == 200
    assert response.json() == {"error": "user_input is required"}


def test_ai_response_wrong_method():
    response = client.get("/api/v1/ai_response")#因為/api/v1/ai_response 是post 所以get 會405
    assert response.status_code == 405
#get拿資料 post資料 put更新資料 delete刪資料，這些HTTP method 只能對應特定動作