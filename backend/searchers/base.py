from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchResult:
    url: str | None = None
    title: str | None = None
    snippet: str | None = None
    source: str = "unknown"


@dataclass
class SearchResponse:
    query: str
    results: list[SearchResult] = field(default_factory=list)
    source: str = "unknown"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None
