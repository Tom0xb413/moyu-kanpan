"""统一数据模型。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _r(x: float) -> float:
    """价格压缩：避免 1e-8 之类噪声，同时保留小币精度。"""
    if x == 0 or x != x:
        return 0.0
    a = abs(x)
    if a >= 1000:
        return round(x, 2)
    if a >= 1:
        return round(x, 4)
    if a >= 0.01:
        return round(x, 6)
    return float(f"{x:.10g}")


@dataclass(slots=True)
class Bar:
    t: int  # 开盘时间，UTC 秒
    o: float
    h: float
    l: float
    c: float
    v: float  # 计价货币成交额（Gate 的 sum / 腾讯的成交量）

    def wire(self) -> list[Any]:
        return [self.t, _r(self.o), _r(self.h), _r(self.l), _r(self.c), _r(self.v)]


@dataclass(slots=True)
class Quote:
    symbol: str
    last: float
    chg_pct: float
    high: float
    low: float
    volume: float
    ts: int
    name: str = ""
    source: str = ""
    funding: float | None = None
    mark: float | None = None
    index_price: float | None = None
    extra: dict[str, Any] | None = None

    def wire(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "symbol": self.symbol,
            "name": self.name,
            "last": _r(self.last),
            "chgPct": round(self.chg_pct, 4),
            "high": _r(self.high),
            "low": _r(self.low),
            "vol": _r(self.volume),
            "ts": self.ts,
            "source": self.source,
        }
        if self.funding is not None:
            d["funding"] = self.funding
        if self.mark is not None:
            d["mark"] = _r(self.mark)
        if self.index_price is not None:
            d["indexPrice"] = _r(self.index_price)
        if self.extra:
            d["extra"] = self.extra
        return d


def clean_bars(bars: list[Bar]) -> list[Bar]:
    """升序 + 去重（lightweight-charts 硬要求）。"""
    bars.sort(key=lambda b: b.t)
    out: list[Bar] = []
    last = -1
    for b in bars:
        if b.t <= last:
            continue
        if b.c <= 0 or b.o <= 0 or b.h <= 0 or b.l <= 0:
            continue
        last = b.t
        out.append(b)
    return out


def to_utc_day(date_str: str) -> int:
    """'2026-11-11' -> 该日 00:00 UTC 的时间戳。"""
    from datetime import datetime, timezone

    y, m, d = (int(p) for p in date_str.split("-")[:3])
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


def to_utc_week(date_str: str) -> int:
    """'2026-11-11' -> 所在 ISO 周（周一）00:00 UTC 的时间戳。

    腾讯周线标的是「本周最后一个交易日」（周五，或本周内的今天），同一周会每天变化，
    直接拿来当 key 会导致增量合并反复追加；统一归一到周一。
    """
    from datetime import datetime, timezone

    y, m, d = (int(p) for p in date_str.split("-")[:3])
    dt = datetime(y, m, d, tzinfo=timezone.utc)
    return int(dt.timestamp()) - dt.weekday() * 86400
