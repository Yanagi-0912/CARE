import pytest

from app.services.rag.whitelist import (
    DEFAULT_ALLOWED_DOMAIN_SUFFIXES,
    WHITELIST_SEARCH_SITE_FILTER,
    InvalidUrl,
    UrlNotAllowedError,
    UrlPolicy,
    assert_allowed_urls,
    is_allowed_url,
    normalize_url,
    parse_allowed_suffixes,
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
        # 數字開頭的標籤：165.npa.gov.tw 是實際的內政部警政署反詐騙專線網域，
        # 標籤邊界比對不能因為子網域以數字開頭就誤判。
        "https://165.npa.gov.tw/",
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
        # --- 5.6 標籤邊界：evilgov.tw 沒有標籤分隔符，不得被 "gov.tw" 誤判為子網域 ---
        "https://evilgov.tw/",
        # --- 5.1 反斜線：Python 的 urlsplit 把 '\' 當一般 host 字元放行，
        #     但 Node（Firecrawl 實際抓取用）與瀏覽器都把 '\' 當路徑分隔符，
        #     等於實際連去 evil.com。這兩個案例是本 change 的核心迴歸，
        #     動手前已在舊碼上確認為紅（見 task-1-report.md 的 TDD Evidence）。
        r"https://evil.com\.gov.tw/page",
        r"https://evil.com\@x.gov.tw/",
        # --- 5.2 百分比編碼：字面上的 host 仍以 ".gov.tw" 結尾，
        #     標籤邊界比對若不擋 '%'，會被這類字串騙過。
        "https://evil.com%5C.gov.tw/page",
        "https://hpa.gov.tw%2egov.tw/",
        "https://www.hpa.gov.tw%2f.evil.com/",
        # --- 5.3 控制字元與空白：urlsplit 會靜默刪除 tab／CR／LF，
        #     "evil.com\t.gov.tw" 正規化後會變成合法的 "evil.com.gov.tw"，
        #     所以必須在剖析前就擋，舊碼在這裡是紅的。
        "https://evil.com\t.gov.tw/x",
        "https://a.gov.tw\r\n.evil.com/",
        "https://www.hpa.gov.tw/a b",
        "https://evil.com\xa0.gov.tw/x",
        # --- 5.4 userinfo：'@' 前後兩邊解析器對「誰是 host」意見不一致，
        #     且畫面上人眼會誤讀成 hpa.gov.tw。
        "https://www.hpa.gov.tw@evil.com/x",
        "https://www.hpa.gov.tw:pass@evil.com/",
        # --- 5.5 IDN／非 ASCII authority：本 change 一律拒絕（reason=malformed），
        #     因為 Python 的 idna 編碼會把這些全形句點類字元當標籤分隔符，
        #     與現行「拿原始 unicode 字串 endswith」的做法會誤判。
        "https://evil.com。gov.tw/",
        "https://台灣.gov.tw/x",
        # --- code review Important 4：host 正字集規則。這些字元在 DNS 上
        #     解析不到、Node／瀏覽器都拒絕剖析，光靠 '%' 檢查擋不到它們——
        #     必須是正向規則（只允許已知安全字元）才能一次涵蓋。
        "https://evil.com<.gov.tw/x",
        "https://evil.com>.gov.tw/x",
        "https://evil.com^.gov.tw/x",
        "https://evil.com|.gov.tw/x",
        "https://.gov.tw/",  # 空標籤（開頭）
        "https://a..gov.tw/",  # 空標籤（中間）
        # --- 歸檔前查證補上：規格要求 host 至少含一個 '.'。單標籤主機名不是
        #     公開網址，應歸為 malformed 而非 not_allowed——兩者在安全上等價
        #     （都拒絕），但原因碼會被下游對映成表單錯誤訊息。
        "https://localhost/x",
        "https://gov/x",
        "http://intranet:8080/x",
        # --- code review Important 3 的補充案例：userinfo 順序與 5.4 相反
        #     （合法 host 在 '@' 後面）。拿掉 userinfo 檢查的話，
        #     parsed.hostname 會自動把 'evil.com@' 這段當帳密丟掉，host 變成
        #     "www.hpa.gov.tw"（合法），會被誤判通過——5.4 那兩個案例是因為
        #     host 變成 evil.com（not_allowed）或 port 解析拋錯而被拒，
        #     跟這道 userinfo 檢查本身無關，無法用來釘住它。
        "https://evil.com@www.hpa.gov.tw/x",
        # --- code review Important 1 的補充案例：控制字元放在 path（而非
        #     host），確保這個案例只靠「剖析前的控制字元前置檢查」才會被擋。
        #     若放在 host 裡，Important 4 新增的 host 正字集規則也會擋下它，
        #     那就無法用來單獨釘住控制字元前置檢查這一道防線了。
        "https://www.hpa.gov.tw/x\x00",
        "https://www.hpa.gov.tw/x\x7f",
    ],
)
def test_is_allowed_url_rejects_non_whitelist(url):
    assert is_allowed_url(url) is False


def test_with_whitelist_site_filter_appends_gov_tw():
    assert with_whitelist_site_filter("我又胃痛") == f"我又胃痛 {WHITELIST_SEARCH_SITE_FILTER}"
    assert with_whitelist_site_filter("胃痛 site:hpa.gov.tw") == "胃痛 site:hpa.gov.tw"
    assert with_whitelist_site_filter("  ") == ""


def test_module_level_wrappers_delegate_to_default_policy():
    """normalize_url／assert_allowed_urls 是薄包裝，委派給 default_url_policy()。"""
    assert normalize_url("www.hpa.gov.tw/x") == "https://www.hpa.gov.tw/x"
    assert assert_allowed_urls(["https://www.hpa.gov.tw/x"]) == ["https://www.hpa.gov.tw/x"]


# ---------------------------------------------------------------------------
# 以下全部使用 UrlPolicy(allowed_suffixes=("gov.tw",)) 建構子注入，不碰 settings。
# ---------------------------------------------------------------------------

NORMALIZE_TABLE = [
    ("www.hpa.gov.tw/x", "https://www.hpa.gov.tw/x"),
    ("HTTP://WWW.HPA.GOV.TW/X", "http://www.hpa.gov.tw/X"),
    ("https://hpa.gov.tw./x", "https://hpa.gov.tw/x"),
    ("https://hpa.gov.tw:443/x", "https://hpa.gov.tw/x"),
    ("https://hpa.gov.tw/x#sec", "https://hpa.gov.tw/x"),
    ("https://hpa.gov.tw/x?utm_source=line&nodeid=1", "https://hpa.gov.tw/x?nodeid=1"),
    ("https://hpa.gov.tw", "https://hpa.gov.tw/"),
    ("https://hpa.gov.tw/x/", "https://hpa.gov.tw/x"),
]


@pytest.mark.parametrize("raw, expected", NORMALIZE_TABLE)
def test_normalize_url_matches_expected(raw, expected):
    policy = UrlPolicy(allowed_suffixes=("gov.tw",))
    assert policy.normalize(raw) == expected


@pytest.mark.parametrize("raw, expected", NORMALIZE_TABLE)
def test_normalize_url_is_idempotent(raw, expected):
    policy = UrlPolicy(allowed_suffixes=("gov.tw",))
    once = policy.normalize(raw)
    assert once == expected
    assert policy.normalize(once) == once


def test_normalize_url_collapses_multiple_trailing_slashes():
    """code review Minor：/x// 只是多打一個斜線，應收斂成 /x，不該被當成格式錯誤。"""
    policy = UrlPolicy(allowed_suffixes=("gov.tw",))
    assert policy.normalize("https://hpa.gov.tw/x//") == "https://hpa.gov.tw/x"
    assert policy.normalize("https://hpa.gov.tw///") == "https://hpa.gov.tw/"


@pytest.mark.parametrize(
    "raw",
    ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,x"],
)
def test_normalize_url_returns_none_for_non_http_scheme(raw):
    policy = UrlPolicy(allowed_suffixes=("gov.tw",))
    assert policy.normalize(raw) is None


def test_parse_allowed_suffixes_collapses_redundant():
    # hpa.gov.tw／cdc.gov.tw／mohw.gov.tw 都被 gov.tw 完全涵蓋，須收斂只留 gov.tw。
    assert parse_allowed_suffixes(
        "gov.tw, hpa.gov.tw ,CDC.GOV.TW,,.mohw.gov.tw"
    ) == ("gov.tw",)


def test_parse_allowed_suffixes_empty_returns_default():
    assert parse_allowed_suffixes("") == DEFAULT_ALLOWED_DOMAIN_SUFFIXES
    assert parse_allowed_suffixes("   ") == DEFAULT_ALLOWED_DOMAIN_SUFFIXES


def test_assert_allowed_urls_reports_all_invalid():
    """走完全部 URL 才一次拋出，且順序與輸入一致（不是遇到第一個就中止）。"""
    policy = UrlPolicy(allowed_suffixes=("gov.tw",))
    urls = [
        r"https://evil.com\.gov.tw/page",  # malformed（剖析前即被反斜線擋下）
        "https://www.hpa.gov.tw/x",  # 合法
        "https://example.com/",  # not_allowed（格式合法但不在白名單）
    ]
    with pytest.raises(UrlNotAllowedError) as exc_info:
        policy.assert_allowed(urls)
    assert exc_info.value.invalid == [
        InvalidUrl(url=urls[0], reason="malformed"),
        InvalidUrl(url=urls[2], reason="not_allowed"),
    ]


def test_assert_allowed_urls_returns_normalized():
    policy = UrlPolicy(allowed_suffixes=("gov.tw",))
    result = policy.assert_allowed(["www.hpa.gov.tw/x?utm_source=line&nodeid=1"])
    assert result == ["https://www.hpa.gov.tw/x?nodeid=1"]
