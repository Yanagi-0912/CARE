"""稽核腳本的分組節流與統計。

重點在 `_check_grouped_by_host`：同一網域一次只打 per_host 個。這不是禮貌
設定而是報告正確性的前提——2026-09-02 首次全庫掃描無此限制，46 個
www.fda.gov.tw 網址被記成「判不出來」，逐一低速重測 6/6 全回 200。
"""

import pytest

from scripts.audit_source_links import (
    _check_grouped_by_host,
    _display_width,
    summarize,
)


class RecordingChecker:
    """記錄每一次 alive() 收到的批次，供斷言批次大小與分組。"""

    def __init__(self, dead=()):
        self._dead = set(dead)
        self.batches: list[list[str]] = []

    async def alive(self, urls):
        urls = list(urls)
        self.batches.append(urls)
        return {u: u not in self._dead for u in urls}


async def test_batches_never_exceed_per_host_limit():
    urls = [f"https://www.hpa.gov.tw/{i}" for i in range(7)]
    checker = RecordingChecker()

    await _check_grouped_by_host(checker, urls, per_host=3)

    assert [len(b) for b in checker.batches] == [3, 3, 1]


async def test_each_batch_contains_a_single_host():
    urls = [
        "https://www.hpa.gov.tw/a",
        "https://www.fda.gov.tw/b",
        "https://www.hpa.gov.tw/c",
        "https://www.fda.gov.tw/d",
    ]
    checker = RecordingChecker()

    await _check_grouped_by_host(checker, urls, per_host=2)

    for batch in checker.batches:
        hosts = {u.split("/")[2] for u in batch}
        assert len(hosts) == 1


async def test_every_url_is_checked_exactly_once():
    urls = [f"https://www.hpa.gov.tw/{i}" for i in range(5)] + [
        "https://www.fda.gov.tw/x"
    ]
    checker = RecordingChecker()

    result = await _check_grouped_by_host(checker, urls, per_host=2)

    checked = [u for batch in checker.batches for u in batch]
    assert sorted(checked) == sorted(urls)
    assert set(result) == set(urls)


async def test_verdicts_survive_the_merge():
    dead = "https://www.fda.gov.tw/gone"
    urls = ["https://www.hpa.gov.tw/a", dead]

    result = await _check_grouped_by_host(RecordingChecker(dead=[dead]), urls, 3)

    assert result == {"https://www.hpa.gov.tw/a": True, dead: False}


def test_summarize_counts_urls_and_chunks_per_verdict():
    rows = [
        {"url": "a", "chunks": 10, "verdict": "alive"},
        {"url": "b", "chunks": 3, "verdict": "dead"},
        {"url": "c", "chunks": 5, "verdict": "inconclusive"},
        {"url": "d", "chunks": 2, "verdict": "alive"},
    ]

    summary = summarize(rows)

    assert summary["alive"] == {"urls": 2, "chunks": 12}
    assert summary["dead"] == {"urls": 1, "chunks": 3}
    assert summary["inconclusive"] == {"urls": 1, "chunks": 5}


def test_display_width_counts_cjk_as_two_columns():
    """報告的欄位標題全是中文，用 len() 會讓每一列對不齊。"""
    assert _display_width("判不出來") == 8
    assert _display_width("chunk") == 5
    assert _display_width("死") == 2
