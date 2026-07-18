from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WebSearchHit:
    title: str
    url: str
    description: str = ""


class WebSearchClient(Protocol):
    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchHit]: ...

    async def scrape(self, url: str) -> str: ...
