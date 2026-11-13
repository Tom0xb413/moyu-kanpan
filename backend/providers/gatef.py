"""Gate.io USDT 永续合约（主源）：加密 + 453 只美股 + 指数 + 金属 + 外汇 + 商品，全周期。"""
from __future__ import annotations

import logging
import time
from typing import Any

from .. import cache, config
from ..models import Bar, Quote, clean_bars
from ..net import ProviderError, net
from .base import BaseProvider

log = logging.getLogger("gatef")

TYPE_MARKET = {
    "": "crypto",
    "stocks": "us",
    "indices": "index",
    "metals": "metal",
    "forex": "fx",
    "commodities": "commodity",
}


class GateFutures(BaseProvider):
    name = "gf"
    label = "合约"
    periods = list(config.PERIODS)

    @staticmethod
    def _parse(rows: list[dict[str, Any]] | None, code: str) -> list[Bar]:
        bars: list[Bar] = []
        for r in rows or []:
            try:
                bars.append(
                    Bar(
                        int(r["t"]),
                        float(r["o"]),
                        float(r["h"]),
                        float(r["l"]),
                        float(r["c"]),
                        float(r.get("sum") or 0.0),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        bars = clean_bars(bars)
        if not bars:
            raise ProviderError(f"{code} K 线解析为空", "EMPTY", 404)
        return bars

    async def klines(self, code: str, period: str, limit: int) -> list[Bar]:
        interval = config.GATE_INTERVAL[period]
        limit = max(30, min(int(limit or config.DEFAULT_LIMIT), config.MAX_BARS))
        url = (
            f"{config.GATE_BASE}/futures/usdt/candlesticks"
            f"?contract={code}&interval={interval}&limit={limit}"
        )
        rows = await net.get_json(
            url,
            provider="gf",
            headers=config.HDR_GATE,
            timeout=config.KLINE_TIMEOUT,
            retry=config.KLINE_RETRY,
        )
        return self._parse(rows, code)

    async def klines_before(self, code: str, period: str, before: int, count: int) -> list[Bar]:
        """取 before 之前（不含）的 count 根：Gate 用 to + limit（to 含端点）。"""
        interval = config.GATE_INTERVAL[period]
        n = max(30, min(int(count or config.PAGE_BARS), config.PAGE_BARS))
        url = (
            f"{config.GATE_BASE}/futures/usdt/candlesticks"
            f"?contract={code}&interval={interval}&to={max(1, int(before) - 1)}&limit={n}"
        )
        rows = await net.get_json(
            url,
            provider="gf",
            headers=config.HDR_GATE,
            timeout=config.KLINE_TIMEOUT,
            retry=config.KLINE_RETRY,
        )
        if not rows:
            return []          # 已经翻到上市之前，正常结束
        bars = self._parse(rows, code)
        return [b for b in bars if b.t < int(before)][-n:]

    async def quote(self, code: str) -> Quote:
        url = f"{config.GATE_BASE}/futures/usdt/tickers?contract={code}"
        rows = await net.get_json(url, provider="gf", headers=config.HDR_GATE)
        if not rows:
            raise ProviderError(f"{code} 无报价", "NOT_FOUND", 404)
        r = rows[0]
        bridge = self.peek_bridge().get(code, {})
        f = lambda k, d=0.0: float(r.get(k) or d)
        return Quote(
            symbol=f"gf:{code}",
            name=bridge.get("name") or code.replace("_", "/"),
            last=f("last"),
            chg_pct=f("change_percentage"),
            high=f("high_24h"),
            low=f("low_24h"),
            volume=f("volume_24h_quote"),
            ts=int(r.get("t") or time.time()),
            source="gatef",
            funding=f("funding_rate"),
            mark=f("mark_price"),
            index_price=f("index_price"),
            extra={"markType": r.get("mark_type", "")},
        )

    async def catalog(self) -> list[dict[str, Any]]:
        def load():
            return net.get_json(f"{config.GATE_BASE}/futures/usdt/contracts", provider="gf", headers=config.HDR_GATE)

        data, _, _ = await cache.cache.run("gate:contracts", config.TTL["index"], load, stale_ok=False)
        out = []
        for c in data:
            code = c.get("name") or ""
            if not code or c.get("in_delisting"):
                continue
            if c.get("status") not in (None, "trading"):
                continue
            mkt = TYPE_MARKET.get(c.get("contract_type") or "", "crypto")
            base = code.split("_")[0]
            out.append(
                {
                    "symbol": f"gf:{code}",
                    "code": code,
                    "base": base,
                    "market": mkt,
                    "name": base if mkt != "crypto" else code.replace("_", "/"),
                    "provider": "gf",
                    "periods": self.periods,
                    "leverage": c.get("leverage_max"),
                }
            )
        log.info("Gate 合约清单 %d 条", len(out))
        return out

    async def bridge(self) -> dict[str, dict[str, Any]]:
        """code -> 元信息（名称/市场）。"""
        def load():
            return net.get_json(f"{config.GATE_BASE}/futures/usdt/contracts", provider="gf", headers=config.HDR_GATE)

        data, _, _ = await cache.cache.run("gate:contracts", config.TTL["index"], load, stale_ok=True)
        return {
            c.get("name"): {
                "name": (c.get("name") or "").split("_")[0],
                "market": TYPE_MARKET.get(c.get("contract_type") or "", "crypto"),
            }
            for c in data
            if c.get("name")
        }

    def peek_bridge(self) -> dict[str, dict[str, Any]]:
        """非阻塞版：只读内存缓存，冷启动时不拖慢首屏。"""
        e = cache.cache.peek("gate:contracts")
        if not e:
            return {}
        return {
            c.get("name"): {
                "name": (c.get("name") or "").split("_")[0],
                "market": TYPE_MARKET.get(c.get("contract_type") or "", "crypto"),
            }
            for c in e.value
            if c.get("name")
        }

    async def warm(self) -> None:
        try:
            await self.bridge()
        except Exception as exc:
            log.info("预热合约清单失败: %s", exc)


gatef = GateFutures()
