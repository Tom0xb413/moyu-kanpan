"""Symbol 解析、能力矩阵、K 线本地库 + 降级链。"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from . import cache, config, kstore, prefs
from .models import Bar
from .net import ProviderError
from .providers import FALLBACK, MARKET_LABEL, REGISTRY, TAG, gatef, tencent

log = logging.getLogger("symbols")


def parse(symbol: str) -> tuple[str, str]:
    if ":" not in symbol:
        raise ProviderError(f"非法 symbol：{symbol}", "BAD_SYMBOL", 400)
    provider, code = symbol.split(":", 1)
    provider = provider.strip().lower()
    code = code.strip()
    if provider not in REGISTRY or not code:
        raise ProviderError(f"未知数据源：{provider}", "BAD_SYMBOL", 400)
    return provider, code


def periods_for(symbol: str) -> list[str]:
    provider, _ = parse(symbol)
    return list(getattr(REGISTRY[provider], "periods", []))


def _tx_name(symbol: str, code: str) -> str:
    """腾讯真实盘的中文名：本次会话搜索结果 -> 规范化 symbol -> 本地偏好。"""
    canon = f"tx:{tencent.normalize_code(code)}"
    name = tencent.NAMES.get(symbol) or tencent.NAMES.get(canon) or ""
    if name:
        return name
    try:
        p = prefs.load()
        for key in ("recent", "watch", "strip"):
            for it in p.get(key) or []:
                n = it.get("name") or ""
                if it.get("symbol") == symbol and n and not n.isascii():
                    tencent.NAMES[symbol] = n
                    return n
    except Exception:
        pass
    return ""


def _market_of(provider: str, code: str) -> str:
    if provider == "tx":
        if code.startswith("hk"):
            return "hk"
        if code.startswith("us"):
            return "us"
        return "cn"
    return "crypto"


async def meta(symbol: str) -> dict[str, Any]:
    provider, code = parse(symbol)
    market = _market_of(provider, code)
    name = code.replace("_", "/")
    if provider in ("gf", "gate"):
        try:
            info = gatef.peek_bridge().get(code)
            if info:
                market = info.get("market") or "crypto"
                base = info.get("name") or code.split("_")[0]
                name = base if market != "crypto" else code.replace("_", "/")
        except Exception:
            pass
    elif provider == "tx":
        # 中文名优先；旧收藏里的裸码（tx:588200）也能对上规范化后的名字
        name = _tx_name(symbol, code)
        if not name:
            name = code[2:].split(".")[0].upper() if code.lower().startswith("us") else code.upper()
    return {
        "symbol": symbol,
        "provider": provider,
        "code": code,
        "name": name,
        "market": market,
        "marketLabel": MARKET_LABEL.get(market, market),
        "tag": TAG.get(provider, provider),
        "periods": periods_for(symbol),
    }


# ---------------- 本地库辅助 ----------------

def _pack(st: dict[str, Any], served: str, stale: bool) -> dict[str, Any]:
    return {
        "symbol": st["symbol"],
        "period": st["period"],
        "provider": st.get("provider") or "gf",
        "at": float(st.get("at") or time.time()),
        "bars": st["bars"],
        "servedFrom": served,
        "stale": stale,
    }


def _fresh(st: dict[str, Any], period: str, now: float, provider: str) -> bool:
    """本地库是否可以直接当新数据用。"""
    if not st or not st.get("bars"):
        return False
    if now - float(st.get("at") or 0) > config.TTL.get(period, 90):
        return False
    if provider == "tx":
        # 周末/长假最后交易日不连续是正常的，只看 at
        return True
    psec = config.PERIOD_SEC[period]
    return now - st["bars"][-1][0] <= psec * 1.5 + 300


def _drift(old: list[list[float]], new: list[list[float]]) -> bool:
    """前复权漂移：重叠柱（排除最后一根未完成柱）收盘价大面积变化。"""
    if len(old) < 3 or len(new) < 3:
        return False
    om = {int(b[0]): b for b in old}
    last_t = max(int(b[0]) for b in new)
    pairs = [(om[int(b[0])], b) for b in new if int(b[0]) in om and int(b[0]) != last_t]
    if len(pairs) < 2:
        return False
    diff = 0
    for a, b in pairs:
        tol = max(1e-6, abs(b[4]) * 1e-3)
        if abs(a[4] - b[4]) > tol:
            diff += 1
    return diff / len(pairs) > 0.2


async def _fetch_chain(provider: str, code: str, period: str, limit: int):
    """按 provider + FALLBACK 依次抓取，返回 (used_provider, wire) 或 None。"""
    last: Exception | None = None
    for pname in [provider] + FALLBACK.get(provider, []):
        try:
            bars: list[Bar] = await REGISTRY[pname].klines(code, period, limit)
            return pname, [b.wire() for b in bars]
        except ProviderError as exc:
            last = exc
            if exc.status == 429:
                raise
            log.info("%s %s %s 失败(%s)，尝试下一个源", pname, code, period, exc.code)
            continue
    if last:
        log.info("%s %s %s 无可用源：%s", provider, code, period, last)
    return None


async def _resolve(symbol: str, provider: str, code: str, period: str, force: bool) -> dict[str, Any]:
    ttl = config.TTL.get(period, 90)
    psec = config.PERIOD_SEC[period]
    now = time.time()
    st = kstore.load(symbol, period)

    # 1) 本地命中：零网络
    if st and not force and _fresh(st, period, now, provider):
        return _pack(st, "local", False)

    # 2) 决定抓多少：增量补尾巴 or 全量
    mode = "full"
    req_limit = config.DEFAULT_LIMIT
    if st and st.get("bars"):
        if provider == "tx":
            mode = "tx_inc"
            req_limit = 60                      # 腾讯下限就是 60
        else:
            gap = int(math.ceil(max(0.0, now - st["bars"][-1][0]) / psec))
            if gap < config.DEFAULT_LIMIT:
                mode = "inc"
                req_limit = max(30, gap + 4)

    # 3) 上游链
    got = await _fetch_chain(provider, code, period, req_limit)
    if got is None:
        if st and st.get("bars"):
            log.warning("%s %s 上游失败，使用本地缓存", symbol, period)
            return _pack(st, "local", True)
        raise ProviderError("无可用数据源", "PROVIDER_DOWN", 502)
    used, wire = got

    # 4) 合并：增量连续性 / 复权漂移 / 全量替换
    merged = wire
    if st and st.get("bars"):
        old = st["bars"]
        if mode == "tx_inc":
            if _drift(old, wire):
                log.info("%s %s 前复权漂移，改全量替换", symbol, period)
                full = await _fetch_chain(provider, code, period, 640)
                if full:
                    used, merged = full
                else:
                    log.warning("%s %s 全量替换失败，保留本地缓存", symbol, period)
                    return _pack(st, "local", True)
            else:
                merged = kstore.merge(old, wire)
        elif mode == "inc":
            contiguous = bool(wire) and wire[0][0] <= old[-1][0] + psec * 1.5
            if contiguous:
                merged = kstore.merge(old, wire)
            else:
                log.info("%s %s 增量有洞，改全量", symbol, period)
                full = await _fetch_chain(provider, code, period, config.DEFAULT_LIMIT)
                if full:
                    used, merged = full
                else:
                    log.warning("%s %s 全量补齐失败，保留本地缓存", symbol, period)
                    return _pack(st, "local", True)

    merged = merged[-config.STORE_MAX_BARS:]
    if not merged:
        raise ProviderError(f"{code} K 线为空", "EMPTY", 404)

    at = time.time()
    payload = {
        "symbol": symbol,
        "period": period,
        "provider": used,
        "at": at,
        "bars": merged,
        "servedFrom": "network",
        "stale": False,
    }
    if st is None or kstore.needs_write(st, merged, at):
        kstore.save(symbol, period, used, merged, at=at)
    else:
        kstore.touch(symbol, period, at)
    return payload


async def fetch_klines(symbol: str, period: str, limit: int, force: bool = False) -> dict[str, Any]:
    provider, code = parse(symbol)
    prov = REGISTRY[provider]
    if not prov.supports(period):
        raise ProviderError(
            "该标的仅支持日线 / 周线（1D / 1W）",
            "PERIOD_UNSUPPORTED",
            400,
        )
    limit = max(30, min(int(limit or config.DEFAULT_LIMIT), config.MAX_BARS))
    ttl = config.TTL.get(period, 90)
    key = f"kline:{symbol}:{period}"          # limit 不进 key，避免同一标的多份缓存

    async def load() -> dict[str, Any]:
        return await _resolve(symbol, provider, code, period, force)

    mem_hit = None if force else cache.cache.fresh(key, ttl)
    payload, _entry, fell_back = await cache.cache.run(key, ttl, load, force=force, stale_ok=True)
    bars = payload["bars"][-limit:]
    depth = len(payload["bars"])

    info = await meta(symbol)
    src = payload.get("provider") or provider
    if fell_back:
        served = "cache"
    elif mem_hit is not None and payload.get("servedFrom") == "network":
        served = "cache"
    else:
        served = payload.get("servedFrom", "network")
    return {
        "symbol": symbol,
        "name": info["name"],
        "market": info["market"],
        "marketLabel": info["marketLabel"],
        "period": period,
        "periods": info["periods"],
        "provider": src,
        "servedFrom": served,
        "sourceLabel": TAG.get(src, src),
        "fetchedAt": int(payload.get("at") or time.time()),
        "ttl": ttl,
        "stale": bool(payload.get("stale") or fell_back),
        "count": len(bars),
        "depth": depth,
        "hasMore": depth > len(bars) or depth < config.STORE_MAX_BARS,
        "bars": bars,
    }


# ---------------- 历史分页（向左滚动加载更早） ----------------

async def _fetch_before(provider: str, code: str, period: str, before: int, count: int):
    """按 provider + FALLBACK 抓「更早的一页」，返回 (used_provider, wire) 或 None。"""
    last: Exception | None = None
    for pname in [provider] + FALLBACK.get(provider, []):
        prov = REGISTRY[pname]
        fn = getattr(prov, "klines_before", None)
        if fn is None:
            continue
        try:
            bars: list[Bar] = await fn(code, period, before, count)
            return pname, [b.wire() for b in bars]
        except NotImplementedError:
            continue
        except ProviderError as exc:
            last = exc
            if exc.status == 429:
                raise
            log.info("%s %s %s 历史分页失败(%s)，尝试下一个源", pname, code, period, exc.code)
            continue
    if last:
        log.info("%s %s 历史分页无可用源：%s", code, period, last)
    return None


def _hist_pack(symbol: str, period: str, provider: str, page: list[list[float]],
               has_more: bool, served: str, stale: bool = False) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "period": period,
        "provider": provider,
        "servedFrom": served,
        "stale": stale,
        "count": len(page),
        "hasMore": bool(has_more),
        "bars": page,
    }


async def fetch_history(symbol: str, period: str, before: int,
                        count: int = config.PAGE_BARS) -> dict[str, Any]:
    """取 before（不含）之前的一页 K 线：本地优先，本地不够才联网补，并落盘。

    返回的 bars 是「新增的那一页」（升序），前端只需前插去重。
    """
    provider, code = parse(symbol)
    prov = REGISTRY[provider]
    if not prov.supports(period):
        raise ProviderError("该标的仅支持日线 / 周线", "PERIOD_UNSUPPORTED", 400)
    try:
        before = int(before)
    except (TypeError, ValueError):
        raise ProviderError("before 参数无效", "BAD_PARAM", 400) from None
    if before <= 0:
        raise ProviderError("before 参数无效", "BAD_PARAM", 400)
    count = max(60, min(int(count or config.PAGE_BARS), config.PAGE_BARS))

    st = kstore.load(symbol, period)
    old = st["bars"] if st else []
    depth = len(old)
    older = [b for b in old if b[0] < before]
    page = older[-count:]

    # 1) 本地库已到上限：不再往外扩，避免把最新的柱子挤掉
    if depth >= config.STORE_MAX_BARS:
        return _hist_pack(symbol, period, (st or {}).get("provider") or provider,
                          page, False, "local")

    # 2) 本地这一页够满：直接给（零网络）
    if len(page) >= count:
        return _hist_pack(symbol, period, (st or {}).get("provider") or provider,
                          page, len(older) > len(page), "local")

    # 3) 本地不够：联网补 remaining 根
    want = count - len(page)
    anchor = page[0][0] if page else before
    got = await _fetch_before(provider, code, period, anchor, want)
    if got is None:
        if page:
            return _hist_pack(symbol, period, (st or {}).get("provider") or provider,
                              page, False, "local", stale=True)
        raise ProviderError("历史数据暂时不可用", "PROVIDER_DOWN", 502)

    used, wire = got
    wire = [b for b in wire if b[0] < anchor][-want:]
    fresh = wire + page
    merged = kstore.extend(symbol, period, used, fresh) if fresh else old
    served = "network" if wire else "local"
    has_more = len(wire) >= want and len(merged) < config.STORE_MAX_BARS
    return _hist_pack(symbol, period, used, fresh, has_more, served)


async def fetch_quote(symbol: str) -> dict[str, Any]:
    provider, code = parse(symbol)
    prov = REGISTRY[provider]
    q = await prov.quote(code)
    return q.wire()


async def fetch_quotes(wanted: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """批量报价：腾讯走一次批量接口，其余走带 TTL 的逐只缓存。"""
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if not wanted:
        return items, errors

    tx_codes: list[tuple[str, str]] = []
    tasks: list[tuple[str, str, str]] = []
    for s in wanted:
        try:
            provider, code = parse(s)
        except ProviderError as exc:
            errors.append({"symbol": s, "code": exc.code, "message": exc.message})
            continue
        if provider == "tx":
            tx_codes.append((s, code))
        else:
            tasks.append((s, provider, code))

    if tx_codes:
        from .providers import tencent

        try:
            got = await tencent.quote_many([c for _, c in tx_codes])
            for s, code in tx_codes:
                q = got.get(code)
                if q:
                    d = q.wire()
                    d["symbol"] = s
                    items.append(d)
                else:
                    errors.append({"symbol": s, "code": "EMPTY", "message": "无报价"})
        except Exception as exc:
            for s, _ in tx_codes:
                errors.append({"symbol": s, "code": "PROVIDER_DOWN", "message": str(exc)})

    async def one(s: str, provider: str, code: str) -> None:
        key = f"quote:{s}"
        try:
            prov = REGISTRY[provider]

            async def load():
                return (await prov.quote(code)).wire()

            data, _, _ = await cache.cache.run(key, config.TTL["quote"], load, stale_ok=True)
            data = dict(data)
            data["symbol"] = s
            items.append(data)
        except Exception as exc:
            errors.append({"symbol": s, "code": getattr(exc, "code", "ERROR"), "message": str(exc)})

    if tasks:
        await asyncio.gather(*(one(*t) for t in tasks))
    return items, errors


def capability(symbol: str) -> dict[str, Any]:
    provider, code = parse(symbol)
    return {
        "symbol": symbol,
        "provider": provider,
        "periods": periods_for(symbol),
        "allPeriods": config.PERIODS,
    }


# ---------------- 收藏预热 ----------------

_warm_pending: list[str] = []
_warm_lock: asyncio.Lock | None = None
_warm_running = False


def _lock() -> asyncio.Lock:
    global _warm_lock
    if _warm_lock is None:
        _warm_lock = asyncio.Lock()
    return _warm_lock


async def _warm_symbols(symbols_: list[str], reason: str) -> dict[str, Any]:
    done = skip = 0
    for sym in symbols_:
        try:
            periods = periods_for(sym)
        except ProviderError:
            continue
        for per in periods:
            try:
                await fetch_klines(sym, per, config.DEFAULT_LIMIT, force=False)
                done += 1
            except ProviderError as exc:
                if exc.status == 429:
                    log.warning("预热遇到限流，中止本轮（已完成 %d 项）", done)
                    return {"done": done, "skipped": skip, "rateLimited": True}
                skip += 1
            except Exception:
                skip += 1
            await asyncio.sleep(config.WARM_INTERVAL)
    log.info("预热完成(%s)：%d 项，跳过 %d", reason, done, skip)
    return {"done": done, "skipped": skip, "rateLimited": False}


def kick_warm(symbol: str | None = None) -> None:
    """收藏变更后调用；串行队列，重复 symbol 去重。"""
    global _warm_running
    item = symbol or "__ALL__"
    if item not in _warm_pending:
        _warm_pending.append(item)
    if _warm_running:
        return
    _warm_running = True
    asyncio.create_task(_drain_warm())


async def _drain_warm() -> None:
    global _warm_running
    try:
        async with _lock():
            while _warm_pending:
                item = _warm_pending.pop(0)
                if item == "__ALL__":
                    watch = [
                        w["symbol"]
                        for w in prefs.normalize_list(prefs.load().get("watch"), prefs.WATCH_MAX)
                    ]
                    if watch:
                        await _warm_symbols(watch, "all")
                else:
                    await _warm_symbols([item], "favorite")
    finally:
        _warm_running = False
        if _warm_pending:
            kick_warm()


async def warm_watch() -> dict[str, Any]:
    watch = [
        w["symbol"]
        for w in prefs.normalize_list(prefs.load().get("watch"), prefs.WATCH_MAX)
    ]
    if not watch:
        return {"done": 0, "skipped": 0, "rateLimited": False}
    async with _lock():
        return await _warm_symbols(watch, "startup")
