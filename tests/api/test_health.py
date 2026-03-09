import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app) #開啟假的瀏覽器跟用戶，不用自己開真的瀏覽器測試，測試只會在記憶體跑。

@pytest.mark.parametrize("url, expected_json", [
    ("/",       {"message": "CARE Backend Running"}),     # 給人看：確認後端有開
    ("/health", {"status": "Welcome to CARE Backend!"}),  # 給機器看：K8s / Cloud Run 健康檢查
])
def test_status_endpoints(url, expected_json):
    response = client.get(url)
    assert response.status_code == 200
    assert response.json() == expected_json