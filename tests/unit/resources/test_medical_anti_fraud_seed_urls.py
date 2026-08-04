from pathlib import Path

from app.services.rag.whitelist import is_allowed_url

SEED = Path(__file__).resolve().parents[3] / "resources" / "medical_anti_fraud_seed_urls.txt"


def test_seed_urls_exist_and_allowed():
    assert SEED.is_file()
    urls = [
        line.strip()
        for line in SEED.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(urls) >= 3
    for url in urls:
        assert url.startswith("http://") or url.startswith("https://")
        assert is_allowed_url(url), url
