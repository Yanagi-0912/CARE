import pytest

from scripts.purge_navigation_chunks import (
    NAVIGATION_URLS,
    build_delete_filter,
    purge,
)


class _FakeDeleteResult:
    def __init__(self, n):
        self.deleted_count = n


class _FakeCollection:
    def __init__(self, counts):
        self._counts = counts
        self.deleted_filters = []

    async def count_documents(self, flt):
        url = flt["url"]
        return self._counts.get(url, 0)

    async def delete_many(self, flt):
        self.deleted_filters.append(flt)
        return _FakeDeleteResult(sum(self._counts.values()))


def test_navigation_urls_are_homepages_or_malformed():
    assert "https://www.mohw.gov.tw/" in NAVIGATION_URLS
    assert "https://www.hpa.gov.tw/..." in NAVIGATION_URLS
    # 實際文章頁不得誤入清單
    assert not any("pid=19853" in u for u in NAVIGATION_URLS)


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
