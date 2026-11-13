"""Provider 抽象基类。"""
from __future__ import annotations

from typing import Any, Protocol

from ..models import Bar, Quote


class Provider(Protocol):
    name: str
    label: str
    periods: list[str]

    async def klines(self, code: str, period: str, limit: int) -> list[Bar]: ...

    async def klines_before(self, code: str, period: str, before: int, count: int) -> list[Bar]: ...

    async def quote(self, code: str) -> Quote: ...

    async def catalog(self) -> list[dict[str, Any]]: ...


class BaseProvider:
    name = "base"
    label = "基础"
    periods: list[str] = []

    def supports(self, period: str) -> bool:
        return period in self.periods

    async def klines_before(self, code: str, period: str, before: int, count: int) -> list[Bar]:
        raise NotImplementedError(f"{self.name} 不支持历史分页")
