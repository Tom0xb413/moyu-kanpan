"""Gate.io 现货（副源）：长尾小币没有合约时回退。"""
from __future__ import annotations

import logging
import time
from typing import Any

from .. import cache, config
from ..models import Bar, Quote, clean_bars
from ..net import ProviderError, net
from .base import BaseProvider

log = logging.getLogger("gate")


class GateSpot(BaseProvider):
    name = "gate"
    label = "现货"
    periods = list(config.PERIODS)

    @staticmethod
    def _parse(rows: list[list[Any]] | None, code: str) -> list[Bar]:
        bars: list[Bar] = []
        for r in rows or []:
            # [ts, quote_vol, close, high, low, open, base_vol, closed]
            try:
                bars.append(
                    Bar(
                        int(r[0]),
                        float(r[5]),
                        float(r[3]),
                        float(r[4]),
                        float(r[2]),
                        float(r[1]),
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        bars = clean_bars(bars)
        if not bars:
            raise ProviderError(f"{code} K 线解析为空", "EMPTY", 404)
        return bars

    async def klines(self, code: str, period: str, limit: int) -> list[Bar]:
        interval = config.GATE_INTERVAL[period]
        limit = max(30, min(int(limit or config.DEFAULT_LIMIT), config.MAX_BARS))
        url = (
            f"{config.GATE_BASE}/spot/candlesticks"
            f"?currency_pair={code}&interval={interval}&limit={limit}"
        )
        rows = await net.get_json(
            url,
            provider="gate",
            headers=config.HDR_GATE,
            timeout=config.KLINE_TIMEOUT,
            retry=config.KLINE_RETRY,
        )
        return self._parse(rows, code)

    async def klines_before(self, code: str, period: str, before: int, count: int) -> list[Bar]:
        interval = config.GATE_INTERVAL[period]
        n = max(30, min(int(count or config.PAGE_BARS), config.PAGE_BARS))
        url = (
            f"{config.GATE_BASE}/spot/candlesticks"
            f"?currency_pair={code}&interval={interval}&to={max(1, int(before) - 1)}&limit={n}"
        )
        rows = await net.get_json(
            url,
            provider="gate",
            headers=config.HDR_GATE,
            timeout=config.KLINE_TIMEOUT,
            retry=config.KLINE_RETRY,
        )
        if not rows:
            return []          # 已经翻到上市之前，正常结束
        bars = self._parse(rows, code)
        return [b for b in bars if b.t < int(before)][-n:]

    async def quote(self, code: str) -> Quote:
        url = f"{config.GATE_BASE}/spot/tickers?currency_pair={code}"
        rows = await net.get_json(url, provider="gate", headers=config.HDR_GATE)
        if not rows:
            raise ProviderError(f"{code} 无报价", "NOT_FOUND", 404)
        r = rows[0]
        return Quote(
            symbol=f"gate:{code}",
            name=code.replace("_", "/"),
            last=float(r.get("last") or 0),
            chg_pct=float(r.get("change_percentage") or 0),
            high=float(r.get("high_24h") or 0),
            low=float(r.get("low_24h") or 0),
            volume=float(r.get("quote_volume") or 0),
            ts=int(time.time()),
            source="gate",
        )

    async def catalog(self) -> list[dict[str, Any]]:
        def load():
            return net.get_json(f"{config.GATE_BASE}/spot/currency_pairs", provider="gate", headers=config.HDR_GATE)

        data, _, _ = await cache.cache.run("gate:pairs", config.TTL["index"], load, stale_ok=False)
        out = []
        for p in data:
            pid = p.get("id") or ""
            if not pid or p.get("trade_status") != "tradable":
                continue
            base = p.get("base") or pid.split("_")[0]
            out.append(
                {
                    "symbol": f"gate:{pid}",
                    "code": pid,
                    "base": base,
                    "market": "crypto",
                    "name": pid.replace("_", "/"),
                    "provider": "gate",
                    "periods": self.periods,
                    "volume": float(p.get("quote_volume") or 0) if p.get("quote_volume") else 0,
                }
            )
        log.info("Gate 现货清单 %d 条", len(out))
        return out


gate_spot = GateSpot()
