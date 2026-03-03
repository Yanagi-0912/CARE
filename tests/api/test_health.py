from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)#fastapi的假瀏覽器，假HTTPclient,這樣就可以不用自己開伺服器 不用開port
#testclient裡面的括號，就是app 整包丟近testclient得到client 物件
#這些呼叫不會真的開 unicorn 或者8080 port，只會在記憶體裡面跑
def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "CARE Backend Running"}#測說根路徑有沒有被改壞
#為啥要跟路近 只是讓你知道後端有開，重點是這是給人看的

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "Welcome to CARE Backend!"}
#給機器看的健康檢查，K8s,cloud run 給監控系統看的