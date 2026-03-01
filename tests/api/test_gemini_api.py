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


def test_ai_response_missing_user_input():
    response = client.post("/api/v1/ai_response", json={})
    assert response.status_code == 422
    assert "detail" in response.json()


def test_ai_response_empty_user_input():
    response = client.post(
        "/api/v1/ai_response",
        json={"user_input": ""},
    )
    assert response.status_code == 200
    assert response.json() == {"error": "user_input is required"}


def test_ai_response_wrong_method():
    response = client.get("/api/v1/ai_response")
    assert response.status_code == 405
