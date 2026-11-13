"""数据 API handlers。"""
from __future__ import annotations

import logging

from aiohttp import web

from . import cache, config, kstore, prefs, search, symbols
from .net import ProviderError, net

log = logging.getLogger("api")


def _err(exc: Exception) -> web.Response:
    if isinstance(exc, ProviderError):
        return web.json_response(
            {"error": {"code": exc.code, "message": exc.message}}, status=exc.status
        )
    log.exception("未处理异常")
    return web.json_response({"error": {"code": "INTERNAL", "message": str(exc)}}, status=500)


async def health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "version": config.VERSION,
            "periods": config.PERIODS,
            "index": len(search.catalog.items),
            "indexError": search.catalog.error,
        }
    )


async def search_handler(request: web.Request) -> web.Response:
    q = request.query.get("q", "").strip()
    limit = min(int(request.query.get("limit", 14) or 14), 40)
    try:
        items = await search.catalog.search(q, limit)
        return web.json_response({"q": q, "items": items})
    except Exception as exc:
        return _err(exc)


async def kline(request: web.Request) -> web.Response:
    symbol = request.query.get("symbol", "")
    period = request.query.get("period", "15m")
    try:
        limit = int(request.query.get("limit", config.DEFAULT_LIMIT) or config.DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = config.DEFAULT_LIMIT
    force = request.query.get("refresh") in ("1", "true")
    before = request.query.get("before")
    if period not in config.PERIODS:
        return web.json_response({"error": {"code": "BAD_PERIOD", "message": period}}, status=400)
    try:
        if before not in (None, "", "0"):
            # 向左滚动：加载 before 之前的一页（本地优先）
            return web.json_response(await symbols.fetch_history(symbol, period, int(before), limit))
        return web.json_response(await symbols.fetch_klines(symbol, period, limit, force=force))
    except Exception as exc:
        return _err(exc)


async def warm(request: web.Request) -> web.Response:
    symbol = (request.query.get("symbol") or "").strip()
    try:
        symbols.kick_warm(symbol or None)
    except Exception as exc:
        return _err(exc)
    return web.json_response({"ok": True}, status=202)


async def quote(request: web.Request) -> web.Response:
    raw = request.query.get("symbols", "").strip()
    if not raw:
        return web.json_response({"items": [], "errors": []})
    wanted = [s for s in raw.split(",") if s][:24]
    try:
        items, errors = await symbols.fetch_quotes(wanted)
        return web.json_response({"items": items, "errors": errors})
    except Exception as exc:
        return _err(exc)


async def ticker(request: web.Request) -> web.Response:
    """滚动条 / 托盘用的报价快照：内存缓存优先，过期才同步刷新。"""
    from . import ticker as ticker_mod

    raw = request.query.get("symbols", "").strip()
    try:
        max_age = float(request.query.get("maxAge", config.TICK_MAX_AGE) or config.TICK_MAX_AGE)
    except ValueError:
        max_age = config.TICK_MAX_AGE
    wanted = [s for s in raw.split(",") if s][: config.STRIP_MAX]
    try:
        data = await ticker_mod.snapshot(wanted, max_age=max_age)
        return web.json_response(data)
    except Exception as exc:
        return _err(exc)


async def capability(request: web.Request) -> web.Response:
    symbol = request.query.get("symbol", "")
    try:
        return web.json_response(symbols.capability(symbol))
    except Exception as exc:
        return _err(exc)


async def symbol_list(request: web.Request) -> web.Response:
    kind = request.query.get("kind", "crypto")
    limit = min(int(request.query.get("limit", 60) or 60), 300)
    try:
        await search.catalog.ensure()
        return web.json_response({"items": search.catalog.by_kind(kind, limit)})
    except Exception as exc:
        return _err(exc)


async def prefs_get(request: web.Request) -> web.Response:
    return web.json_response(prefs.load())


async def prefs_post(request: web.Request) -> web.Response:
    try:
        patch = await request.json()
    except Exception:
        return web.json_response({"error": {"code": "BAD_JSON", "message": "请求体不是 JSON"}}, status=400)
    return web.json_response(prefs.save(patch if isinstance(patch, dict) else {}))


async def diag(request: web.Request) -> web.Response:
    import time

    cat = search.catalog
    return web.json_response(
        {
            "net": net.stats,
            "cacheSize": len(cache.cache._d),
            "store": kstore.stats(),
            "catalog": {
                "items": len(cat.items),
                "ageSec": int(time.time() - cat.loaded_at) if cat.loaded_at else None,
                "error": cat.error,
                "byMarket": {
                    k: sum(1 for it in cat.items if it["market"] == k)
                    for k in ("crypto", "us", "index", "metal", "fx", "commodity")
                },
            },
            "prefs": prefs.load(),
        }
    )
