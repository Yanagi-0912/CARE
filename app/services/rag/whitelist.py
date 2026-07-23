from urllib.parse import urlparse

ALLOWED_DOMAIN_SUFFIXES: tuple[str, ...] = (
    "gov.tw",
    "hpa.gov.tw",
    "cdc.gov.tw",
    "mohw.gov.tw",
)


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
