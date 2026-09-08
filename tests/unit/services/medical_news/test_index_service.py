import pytest

from app.services.medical_news.grader import NewsJudgement
from app.services.medical_news.index_service import DrugNewsIndexService
from app.services.rag.web_client import ScrapedPage, WebSearchHit

ALLOWED = "https://www.fda.gov.tw/TC/newsContent.aspx?id=1"
# 抓回來的內文必須帶得出發布日，否則第 3 道防線（時效）會正確地擋下來。
DATED_BODY = "發布日期 2026-08-30 食藥署公告回收"
BLOCKED = "https://evil.example.com/news/1"


class FakeWebClient:
    def __init__(self, hits_by_query=None, pages=None, search_error=None):
        self._hits = hits_by_query or {}
        self._pages = pages or {}
        self._search_error = search_error
        self.searched: list[str] = []
        self.scraped: list[str] = []

    async def search(self, query, *, limit=5):
        self.searched.append(query)
        if self._search_error is not None:
            raise self._search_error
        for key, hits in self._hits.items():
            if key in query:
                return list(hits)
        return []

    async def scrape_page(self, url):
        self.scraped.append(url)
        return self._pages.get(url, ScrapedPage(text=DATED_BODY, final_url=url))


class FakeGrader:
    def __init__(self, judgement=None, error=None, by_drug=None):
        self._judgement = judgement or NewsJudgement(True, "recall", "食藥署公告回收。")
        self._error = error
        self._by_drug = by_drug or {}
        self.calls: list[tuple[str, str]] = []

    async def judge(self, drug_key, title, text):
        self.calls.append((drug_key, title))
        if drug_key in self._by_drug:
            outcome = self._by_drug[drug_key]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if self._error is not None:
            raise self._error
        return self._judgement


class FakeMedicationRepo:
    def __init__(self, keys):
        self._keys = keys

    async def list_active_drug_keys(self, date_str, collection=None):
        return list(self._keys)


class FakeNewsRepo:
    def __init__(self):
        self.stored = []

    async def upsert_by_url(self, news, collection=None):
        self.stored.append(news)
        return True


def _service(web_client, grader, keys=("普拿疼",), news_repo=None, **kwargs):
    return DrugNewsIndexService(
        web_client=web_client,
        grader=grader,
        repository=news_repo or FakeNewsRepo(),
        medication_repository=FakeMedicationRepo(keys),
        max_age_days=kwargs.pop("max_age_days", 30),
        search_limit=kwargs.pop("search_limit", 5),
        **kwargs,
    )


def _hit(url=ALLOWED, title="普拿疼回收公告", description="食藥署公告普拿疼回收"):
    return WebSearchHit(title=title, url=url, description=description)


# ── 防線順序 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_whitelisted_url_is_dropped_before_scrape():
    """非官方域在搜尋階段就被排除，不得付出抓取成本。"""
    client = FakeWebClient(hits_by_query={"普拿疼": [_hit(url=BLOCKED)]})
    grader = FakeGrader()
    service = _service(client, grader)

    await service.run_once("2026-09-02")

    assert client.scraped == []
    assert grader.calls == []


@pytest.mark.asyncio
async def test_literal_mismatch_is_dropped_before_grader():
    """字面比對必須在 LLM 之前執行。

    這個順序光看實作看不出有沒有被後人調換，只能用「grader 未被呼叫」來鎖住。
    調換之後功能完全正常，只是每天多花一筆本來不必花的模型費用。
    """
    client = FakeWebClient(
        hits_by_query={
            "普拿疼": [_hit(title="冠脂妥相關公告", description="與冠脂妥有關")]
        }
    )
    grader = FakeGrader()
    service = _service(client, grader)

    await service.run_once("2026-09-02")

    assert grader.calls == []
    assert client.scraped == []


@pytest.mark.asyncio
async def test_relevant_hit_reaches_grader_and_is_stored():
    client = FakeWebClient(
        hits_by_query={"普拿疼": [_hit()]},
        pages={ALLOWED: ScrapedPage(text=DATED_BODY, final_url=ALLOWED)},
    )
    grader = FakeGrader()
    repo = FakeNewsRepo()
    service = _service(client, grader, news_repo=repo)

    result = await service.run_once("2026-09-02")

    assert len(grader.calls) == 1
    assert len(repo.stored) == 1
    assert repo.stored[0].drug_key == "普拿疼"
    assert repo.stored[0].concern_kind == "recall"
    assert result.stored == 1


# ── fail closed ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grader_exception_skips_only_that_drug():
    """單一藥品的判定失敗不得中斷整輪。"""
    client = FakeWebClient(
        hits_by_query={
            "普拿疼": [_hit()],
            "冠脂妥": [_hit(title="冠脂妥回收公告", description="食藥署公告冠脂妥回收")],
        }
    )
    grader = FakeGrader(by_drug={"普拿疼": TimeoutError("boom")})
    repo = FakeNewsRepo()
    service = _service(client, grader, keys=("普拿疼", "冠脂妥"), news_repo=repo)

    result = await service.run_once("2026-09-02")

    assert [n.drug_key for n in repo.stored] == ["冠脂妥"]
    assert result.skipped >= 1


@pytest.mark.asyncio
async def test_search_timeout_does_not_abort_run():
    client = FakeWebClient(search_error=TimeoutError("gov.tw timeout"))
    repo = FakeNewsRepo()
    service = _service(client, FakeGrader(), keys=("普拿疼", "冠脂妥"), news_repo=repo)

    result = await service.run_once("2026-09-02")

    assert repo.stored == []
    assert result.keys_scanned == 2


@pytest.mark.asyncio
async def test_irrelevant_judgement_is_not_stored():
    client = FakeWebClient(hits_by_query={"普拿疼": [_hit()]})
    grader = FakeGrader(judgement=NewsJudgement(False, "recall", "摘要"))
    repo = FakeNewsRepo()
    service = _service(client, grader, news_repo=repo)

    await service.run_once("2026-09-02")

    assert repo.stored == []


@pytest.mark.asyncio
async def test_concern_kind_none_is_not_stored():
    client = FakeWebClient(hits_by_query={"普拿疼": [_hit()]})
    grader = FakeGrader(judgement=NewsJudgement(True, "none", "摘要"))
    repo = FakeNewsRepo()
    service = _service(client, grader, news_repo=repo)

    await service.run_once("2026-09-02")

    assert repo.stored == []


# ── 輸出防線與時效 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_output_guard_violation_is_discarded_not_rewritten():
    """摘要踩到用藥建議紅線時整則丟棄。

    很自然但錯誤的修法是「改寫一下再存」——那等於讓模型再賭一次，而這道防線
    存在的前提正是不能相信模型會自己守住。
    """
    client = FakeWebClient(hits_by_query={"普拿疼": [_hit()]})
    grader = FakeGrader(judgement=NewsJudgement(True, "recall", "建議停藥並回診"))
    repo = FakeNewsRepo()
    service = _service(client, grader, news_repo=repo)

    await service.run_once("2026-09-02")

    assert repo.stored == []


@pytest.mark.asyncio
async def test_missing_published_at_is_not_stored():
    client = FakeWebClient(
        hits_by_query={"普拿疼": [_hit()]},
        pages={ALLOWED: ScrapedPage(text=DATED_BODY, final_url=ALLOWED)},
    )
    service = _service(client, FakeGrader(), news_repo=(repo := FakeNewsRepo()))
    service._extract_published_at = lambda page, hit: None

    await service.run_once("2026-09-02")

    assert repo.stored == []


@pytest.mark.asyncio
async def test_stale_news_is_not_stored():
    client = FakeWebClient(hits_by_query={"普拿疼": [_hit()]})
    service = _service(
        client, FakeGrader(), news_repo=(repo := FakeNewsRepo()), max_age_days=30
    )
    service._extract_published_at = lambda page, hit: "2026-01-01"

    await service.run_once("2026-09-02")

    assert repo.stored == []


# ── 其他 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_query_carries_site_filter():
    """搜尋必須帶白名單 site filter，這是來源限定官方域的第一道。"""
    client = FakeWebClient()
    service = _service(client, FakeGrader())

    await service.run_once("2026-09-02")

    assert client.searched
    assert "site:" in client.searched[0]


@pytest.mark.asyncio
async def test_no_medications_means_no_search():
    client = FakeWebClient()
    service = _service(client, FakeGrader(), keys=())

    result = await service.run_once("2026-09-02")

    assert client.searched == []
    assert result.keys_scanned == 0


@pytest.mark.asyncio
async def test_duplicate_url_across_drugs_is_only_scraped_once():
    """同一則公告常同時提到多種藥；同一輪內不重複抓取與判定。"""
    hit = _hit(title="普拿疼與冠脂妥回收", description="食藥署公告普拿疼與冠脂妥回收")
    client = FakeWebClient(hits_by_query={"普拿疼": [hit], "冠脂妥": [hit]})
    service = _service(client, FakeGrader(), keys=("普拿疼", "冠脂妥"))

    await service.run_once("2026-09-02")

    assert client.scraped.count(ALLOWED) == 1
