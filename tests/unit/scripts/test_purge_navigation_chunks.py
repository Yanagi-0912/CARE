import pytest

from scripts.purge_navigation_chunks import (
    NAVIGATION_URLS,
    build_delete_filter,
    purge,
)


class _FakeDeleteResult:
    def __init__(self, n):
        self.deleted_count = n


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs
        self._limit = None

    def limit(self, n):
        self._limit = n
        return self

    async def to_list(self, length=None):
        docs = self._docs
        if self._limit is not None:
            docs = docs[: self._limit]
        if length is not None:
            docs = docs[:length]
        return docs


class _FakeCollection:
    def __init__(self, counts, docs=None):
        self._counts = counts
        # docs: {url: [ {field: value}, ... ]}
        self._docs = docs or {}
        self.deleted_filters = []

    async def count_documents(self, flt):
        url = flt["url"]
        return self._counts.get(url, 0)

    def find(self, flt):
        url = flt["url"]
        return _FakeCursor(self._docs.get(url, []))

    async def delete_many(self, flt):
        self.deleted_filters.append(flt)
        return _FakeDeleteResult(sum(self._counts.values()))


def test_navigation_urls_include_documented_homepages():
    assert "https://www.mohw.gov.tw/" in NAVIGATION_URLS
    assert "https://www.hpa.gov.tw/..." in NAVIGATION_URLS


def test_navigation_urls_pid_19922_is_the_only_detail_page_exception():
    # NAVIGATION_URLS 應只有一個看起來像正常文章頁（Detail.aspx?...pid=）的
    # 例外（pid=19922，見模組內註解說明：抓到的是導覽骨架而非正文）。
    # 其他 Detail.aspx pid 一律不得混入這份清單，以免誤刪正常文章。
    detail_page_urls = [
        u for u in NAVIGATION_URLS if "Detail.aspx" in u and "pid=" in u
    ]
    assert detail_page_urls == [
        "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=5020&pid=19922"
    ]
    # golden set 裡的正常文章頁 pid 絕不能出現在清單中
    assert not any("pid=16550" in u for u in NAVIGATION_URLS)
    assert not any("pid=17435" in u for u in NAVIGATION_URLS)


def test_build_delete_filter_uses_url_in():
    flt = build_delete_filter(["https://a", "https://b"])
    assert flt == {"url": {"$in": ["https://a", "https://b"]}}


@pytest.mark.asyncio
async def test_purge_dry_run_does_not_delete():
    collection = _FakeCollection({"https://www.mohw.gov.tw/": 114})
    report = await purge(
        collection, ["https://www.mohw.gov.tw/"], apply=False
    )
    assert report["matched"] == 114
    assert report["deleted"] == 0
    assert collection.deleted_filters == []


@pytest.mark.asyncio
async def test_purge_apply_deletes():
    collection = _FakeCollection({"https://www.mohw.gov.tw/": 114})
    report = await purge(
        collection, ["https://www.mohw.gov.tw/"], apply=True
    )
    assert report["deleted"] == 114
    assert collection.deleted_filters == [
        {"url": {"$in": ["https://www.mohw.gov.tw/"]}}
    ]


@pytest.mark.asyncio
async def test_purge_prints_sample_content_in_dry_run(capsys):
    url = "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=5020&pid=19922"
    collection = _FakeCollection(
        {url: 37},
        docs={
            url: [
                {"chunk_content": "[跳到主要內容區塊]" * 10},
                {"chunk_content": "## 新聞"},
            ]
        },
    )
    await purge(collection, [url], apply=False, text_field="chunk_content")
    out = capsys.readouterr().out
    assert "跳到主要內容區塊" in out
    assert "新聞" in out


@pytest.mark.asyncio
async def test_purge_sample_content_is_truncated_to_60_chars(capsys):
    url = "https://www.mohw.gov.tw/"
    long_content = "x" * 200
    collection = _FakeCollection(
        {url: 1},
        docs={url: [{"chunk_content": long_content}]},
    )
    await purge(collection, [url], apply=False, text_field="chunk_content")
    out = capsys.readouterr().out
    # 每一行印出的內容片段不應包含完整 200 字元的長字串
    assert "x" * 200 not in out
    assert "x" * 60 in out


@pytest.mark.asyncio
async def test_purge_prints_sample_content_in_apply_mode_too(capsys):
    url = "https://www.mohw.gov.tw/"
    collection = _FakeCollection(
        {url: 1},
        docs={url: [{"chunk_content": "一站式搜尋"}]},
    )
    await purge(collection, [url], apply=True, text_field="chunk_content")
    out = capsys.readouterr().out
    assert "一站式搜尋" in out


@pytest.mark.asyncio
async def test_purge_no_samples_printed_when_no_match(capsys):
    collection = _FakeCollection({}, docs={})
    report = await purge(
        collection, ["https://nothing-here/"], apply=False
    )
    assert report["matched"] == 0
    out = capsys.readouterr().out
    assert "例：" not in out
