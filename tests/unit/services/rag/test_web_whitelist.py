import pytest

from app.services.rag.whitelist import (
    WHITELIST_SEARCH_SITE_FILTER,
    is_allowed_url,
    with_whitelist_site_filter,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1",
        "https://www.cdc.gov.tw/Category/Page/x",
        "https://www.mohw.gov.tw/cp-16-1.html",
        "https://www.gov.tw/",
        "https://health.gov.tw/news",
        "http://sub.cdc.gov.tw/path",
    ],
)
def test_is_allowed_url_accepts_whitelist_domains(url):
    assert is_allowed_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.google.com/search?q=高血壓",
        "https://example.com/",
        "https://gov.tw.evil.com/",
        "https://notgov.tw.example.com/",
        "not-a-url",
        "",
        "https://",
    ],
)
def test_is_allowed_url_rejects_non_whitelist(url):
    assert is_allowed_url(url) is False


def test_with_whitelist_site_filter_appends_gov_tw():
    assert with_whitelist_site_filter("我又胃痛") == f"我又胃痛 {WHITELIST_SEARCH_SITE_FILTER}"
    assert with_whitelist_site_filter("胃痛 site:hpa.gov.tw") == "胃痛 site:hpa.gov.tw"
    assert with_whitelist_site_filter("  ") == ""
