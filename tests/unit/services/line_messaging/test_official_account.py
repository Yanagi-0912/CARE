"""官方帳號的 basic ID：向 LINE 問一次就記住，失敗不記、下次重問。"""

from types import SimpleNamespace

from app.services.line_messaging.official_account import OfficialAccountService
from tests.conftest import fake_line_token_manager

BOT_INFO = SimpleNamespace(basic_id="@460xmyhp")


def _service(monkeypatch, results, token_manager=None):
    """`results` 依序是每次問 LINE 的結果：物件就回傳、例外就拋出。"""
    calls: list[str] = []

    def fake_fetch(token):
        calls.append(token)
        result = results[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(
        OfficialAccountService, "_fetch_bot_info", staticmethod(fake_fetch)
    )
    service = OfficialAccountService(token_manager or fake_line_token_manager("tok"))
    return service, calls


async def test_returns_basic_id_from_bot_info(monkeypatch):
    service, calls = _service(monkeypatch, [BOT_INFO])

    assert await service.get_basic_id() == "@460xmyhp"
    assert calls == ["tok"]


async def test_second_call_uses_the_cached_id(monkeypatch):
    service, calls = _service(monkeypatch, [BOT_INFO])

    await service.get_basic_id()

    assert await service.get_basic_id() == "@460xmyhp"
    assert len(calls) == 1


async def test_failure_returns_none_and_retries_next_time(monkeypatch):
    service, calls = _service(monkeypatch, [RuntimeError("LINE 503"), BOT_INFO])

    assert await service.get_basic_id() is None
    assert await service.get_basic_id() == "@460xmyhp"
    assert len(calls) == 2


async def test_blank_basic_id_counts_as_failure(monkeypatch):
    service, calls = _service(
        monkeypatch, [SimpleNamespace(basic_id="  "), BOT_INFO]
    )

    assert await service.get_basic_id() is None
    assert await service.get_basic_id() == "@460xmyhp"


async def test_token_failure_returns_none_without_calling_line(monkeypatch):
    service, calls = _service(
        monkeypatch,
        [BOT_INFO],
        token_manager=fake_line_token_manager(side_effect=RuntimeError("token")),
    )

    assert await service.get_basic_id() is None
    assert calls == []
