"""标的索引与搜索：Gate 合约 1086 + 现货 2156 本地索引，腾讯 smartbox 补充 A股/港股。"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from . import aliases
from .providers import MARKET_LABEL, REGISTRY, TAG, gate_spot, gatef, tencent

log = logging.getLogger("search")

HOT = [
    "gf:BTC_USDT",
    "gf:ETH_USDT",
    "gf:SOL_USDT",
    "gf:BNB_USDT",
    "gf:NVDA_USDT",
    "gf:TSLA_USDT",
    "gf:AAPL_USDT",
    "gf:MSFT_USDT",
    "gf:SPY_USDT",
    "gf:QQQ_USDT",
    "gf:PAXG_USDT",
]

_ORDER = {"gf": 0, "gate": 1, "tx": 2}

# 精选：Gate 没有指数/A股的合约，用腾讯真实盘补上（仅日/周线）
CURATED = [
    ("tx:sh000001", "上证指数", "index"),
    ("tx:sz399001", "深证成指", "index"),
    ("tx:sz399006", "创业板指", "index"),
    ("tx:sh000300", "沪深300", "index"),
    ("tx:hkHSI", "恒生指数", "index"),
    ("tx:hkHSTECH", "恒生科技", "index"),
    ("tx:usIXIC", "纳斯达克", "index"),
    ("tx:usINX", "标普500", "index"),
    ("tx:usDJI", "道琼斯", "index"),
]


class Catalog:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.by_symbol: dict[str, dict[str, Any]] = {}
        self.loaded_at = 0.0
        self._lock = asyncio.Lock()
        self.error = ""

    async def ensure(self, force: bool = False) -> None:
        if not force and self.items and time.time() - self.loaded_at < 4 * 3600:
            return
        async with self._lock:
            if not force and self.items and time.time() - self.loaded_at < 4 * 3600:
                return
            items: list[dict[str, Any]] = []
            errs = []
            for prov in (gatef, gate_spot):
                try:
                    items.extend(await prov.catalog())
                except Exception as exc:
                    errs.append(f"{prov.name}: {exc}")
                    log.warning("拉取 %s 清单失败: %s", prov.name, exc)
            if not items:
                self.error = "；".join(errs) or "清单为空"
                if not self.by_symbol:
                    raise RuntimeError(self.error)
                return
            # 去重：同一 base+quote 合约优先
            seen: dict[tuple[str, str], int] = {}
            merged: list[dict[str, Any]] = []
            for it in items:
                code = it["code"]
                quote = code.split("_")[1] if "_" in code else ""
                k = (it["base"].upper(), quote.upper())
                if k in seen:
                    prev = merged[seen[k]]
                    if _ORDER.get(it["provider"], 9) < _ORDER.get(prev["provider"], 9):
                        merged[seen[k]] = it
                    continue
                seen[k] = len(merged)
                merged.append(it)
            for sym, name, market in CURATED:
                if sym.startswith("tx:"):
                    tencent.NAMES[sym] = name
                if any(it["symbol"] == sym for it in merged):
                    continue
                provider = sym.split(":")[0]
                merged.append(
                    {
                        "symbol": sym,
                        "code": sym.split(":", 1)[1],
                        "base": sym.split(":", 1)[1].upper(),
                        "market": market,
                        "name": name,
                        "provider": provider,
                        "periods": list(REGISTRY[provider].periods),
                    }
                )
            self.items = merged
            self.by_symbol = {it["symbol"]: it for it in merged}
            self.loaded_at = time.time()
            self.error = ""
            log.info("标的索引就绪：%d 条", len(merged))

    # ---------- 搜索 ----------
    def _score(self, it: dict[str, Any], q: str) -> int | None:
        code = it["code"].lower()
        base = (it.get("base") or "").lower()
        name = (it.get("name") or "").lower()
        if code == q or base == q:
            return 0
        if code.startswith(q):
            return 1
        if base.startswith(q):
            return 2
        if q in code:
            return 3
        if q in name:
            return 4
        return None

    def local(self, q: str, limit: int) -> list[dict[str, Any]]:
        q = (q or "").strip().lower()
        if not q:
            out = [self.by_symbol[s] for s in HOT if s in self.by_symbol]
            if len(out) < limit:
                for it in self.items:
                    if it["market"] != "crypto":
                        out.append(it)
                    if len(out) >= limit:
                        break
            return out[:limit]
        scored = []
        for it in self.items:
            s = self._score(it, q)
            if s is None:
                continue
            quote_pen = 0 if it["code"].endswith("_USDT") else 1
            scored.append((s, quote_pen, _ORDER.get(it["provider"], 9), it))
        scored.sort(key=lambda t: (t[0], t[2], t[1], len(t[3]["code"])))
        hits = [t[3] for t in scored[: limit * 2]]

        # 别名命中（英伟达 / apple / 纳指）置顶
        front: list[dict[str, Any]] = []
        sym_alias = aliases.lookup_symbol(q)
        if sym_alias and sym_alias in self.by_symbol:
            front.append(self.by_symbol[sym_alias])
        ticker = aliases.lookup_ticker(q)
        if ticker:
            cand = f"gf:{ticker}_USDT"
            if cand in self.by_symbol:
                front.append(self.by_symbol[cand])
            alt = f"gate:{ticker}_USDT"
            if alt in self.by_symbol:
                front.append(self.by_symbol[alt])
        if front:
            seen = set()
            merged = []
            for it in front + hits:
                if it["symbol"] in seen:
                    continue
                seen.add(it["symbol"])
                merged.append(it)
            hits = merged
        return hits[: limit * 2]

    async def search(self, q: str, limit: int = 14) -> list[dict[str, Any]]:
        await self.ensure()
        out = self.local(q, limit)
        ticker = aliases.lookup_ticker(q) if q else ""
        if q and len(q) >= 2 and (len(out) < 6 or ticker):
            try:
                extra = await tencent.search(q, limit=4)
            except Exception:
                extra = []
            have = {it["symbol"] for it in out}
            extra = [it for it in extra if it["symbol"] not in have]
            if extra:
                # 有对应合约时，把「真实盘」紧排在合约后面，方便双轨对照
                out = (out[:1] + extra + out[1:]) if (ticker and out) else (out + extra)
        return [self.decorate(it) for it in out[:limit]]

    @staticmethod
    def decorate(it: dict[str, Any]) -> dict[str, Any]:
        market = it.get("market", "crypto")
        name = it.get("name") or it.get("code", "")
        if market == "us":
            cn = aliases.display_name(it.get("code", ""))
            if cn:
                name = cn + " " + it.get("code", "").split("_")[0]
        return {
            "symbol": it["symbol"],
            "code": it.get("code", ""),
            "name": name,
            "market": market,
            "marketLabel": MARKET_LABEL.get(market, market),
            "provider": it.get("provider", ""),
            "tag": TAG.get(it.get("provider", ""), ""),
            "periods": it.get("periods") or list(REGISTRY[it["provider"]].periods),
        }

    def get(self, symbol: str) -> dict[str, Any] | None:
        it = self.by_symbol.get(symbol)
        return self.decorate(it) if it else None

    def by_kind(self, kind: str, limit: int = 80) -> list[dict[str, Any]]:
        rows = [it for it in self.items if it["market"] == kind]
        rows.sort(key=lambda it: len(it["code"]))
        return [self.decorate(it) for it in rows[:limit]]


catalog = Catalog()
