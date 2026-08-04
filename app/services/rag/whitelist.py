from urllib.parse import urlparse

ALLOWED_DOMAIN_SUFFIXES: tuple[str, ...] = (
    "gov.tw",
    "hpa.gov.tw",
    "cdc.gov.tw",
    "mohw.gov.tw",
)

# Firecrawl 開放搜尋結果多為民間站；加 site: 才能落在白名單內。
# 目前後綴皆屬 *.gov.tw，單一 site:gov.tw 即可涵蓋。
WHITELIST_SEARCH_SITE_FILTER = "site:gov.tw"


def with_whitelist_site_filter(query: str) -> str:
    """為網搜 query 附加白名單 site 限制（已含 site: 則不重複加）。"""
    q = (query or "").strip()
    if not q:
        return q
    if "site:" in q.lower():
        return q
    return f"{q} {WHITELIST_SEARCH_SITE_FILTER}"


def is_allowed_url(url: str) -> bool:
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False
    for suffix in ALLOWED_DOMAIN_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False
