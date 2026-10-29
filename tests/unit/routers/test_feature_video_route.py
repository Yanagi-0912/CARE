"""功能說明示範影片的靜態路由：比照 test_drug_appearance_image_route，用 TestClient
打真實路由與已提交的檔案（resources/feature_videos/，齊不齊由
tests/unit/resources/test_feature_videos.py 守門）。
"""

from fastapi.testclient import TestClient

from app.main import app
from app.services.line_messaging import feature_videos

client = TestClient(app)


def test_serves_a_committed_video():
    video = feature_videos.file_path("zh-TW", "ai", feature_videos.VIDEO_EXT)

    response = client.get(f"{feature_videos.URL_PATH}/zh-TW/ai.mp4")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == video.read_bytes()


def test_serves_the_preview_image():
    response = client.get(f"{feature_videos.URL_PATH}/en/family.jpg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")


def test_answers_range_requests_for_iphone_playback():
    """iPhone 的播放器要能分段讀才肯播 mp4。"""
    response = client.get(
        f"{feature_videos.URL_PATH}/zh-TW/ai.mp4", headers={"Range": "bytes=0-99"}
    )

    assert response.status_code == 206
    assert len(response.content) == 100
    assert response.headers["accept-ranges"] == "bytes"


def test_rejects_unknown_language_section_or_extension():
    for path in [
        "zh-CN/ai.mp4",
        "zh-TW/unknown.mp4",
        "zh-TW/ai.mov",
        "zh-TW/AI.mp4",
        "nan-TW/ai.mp4",
    ]:
        assert client.get(f"{feature_videos.URL_PATH}/{path}").status_code == 404


def test_blocks_path_traversal():
    response = client.get(f"{feature_videos.URL_PATH}/zh-TW/..%2F..%2Fapp%2Fmain.py")

    assert response.status_code == 404
