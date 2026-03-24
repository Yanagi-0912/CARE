from unittest.mock import MagicMock, patch
import pymongo
from pymongo import UpdateOne

from scripts.add_location import main


def _mock_db():
    client = MagicMock()
    db = MagicMock()
    collection = MagicMock()
    client.__getitem__.return_value = db
    db.__getitem__.return_value = collection
    db.name = "CARE_database"
    collection.name = "medicalFacilities"
    return client, collection


def test_update_geojson_and_create_index(capsys):
    # 取得模擬的 MongoDB 客戶端與collection
    client, collection = _mock_db()

    docs = [
        # 緯度超出範圍
        {"_id": 1, "latitude": "99.1", "longitude": "-100"},
        # 緯度不是數字
        {"_id": 2, "latitude": "ABC", "longitude": "121.6"},
        # 正常
        {"_id": 3, "latitude": "0.0", "longitude": "0.0"},
        # 經度超出範圍
        {"_id": 4, "latitude": "90.0", "longitude": "360.0"},
    ]
    # 雖然id=1、4不合理，但 main() 只檢查能否轉 float，不做經緯度範圍驗證，
    # 所以這兩筆資料仍會被視為成功轉換
    collection.count_documents.return_value = 3
    collection.find.return_value = docs
    # patch: 固定 mongodb url，讓 MongoClient 回傳模擬 client
    # ，並避免真的讀取 .env
    with patch(
        "app.dependencies.get_mongodb_url", return_value="mongodb://fake"
    ), patch("scripts.add_location.pymongo.MongoClient", return_value=client), patch(
        "scripts.add_location.load_dotenv"
    ):
        main()
    # main() 只檢查能否轉 float，不做經緯度範圍驗證
    expected_query = {
        "latitude": {"$exists": True, "$ne": ""},
        "longitude": {"$exists": True, "$ne": ""},
    }
    collection.count_documents.assert_called_once_with(expected_query)
    collection.find.assert_called_once_with(expected_query)
    collection.bulk_write.assert_called_once()
    ops = collection.bulk_write.call_args[0][0]
    assert len(ops) == 3
    assert isinstance(ops[0], UpdateOne)
    collection.create_index.assert_called_once_with([("location", pymongo.GEOSPHERE)])

    out = capsys.readouterr().out
    assert "成功連線至資料庫" in out
    assert "成功將 3 筆資料的經緯度轉換為 GeoJSON 格式" in out
