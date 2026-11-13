"""后台报价快照：滚动条 / 托盘 / 隐藏时都在刷新的那一层。

- 内存里保留每个标的最近一次报价与时间，页面来取时先给缓存，过期才同步刷。
- 后台任务按「滚动条标的 + 收藏」滚动刷新，窗口隐藏时降频，不打扰。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from . import config, prefs, symbols

log = logging.getLogger("ticker")

_prices: dict[str, dict[str, Any]] = {}
_at: dict[str, float] = {}
_errors: dict[str, str] = {}
_symbols: list[str] = []
_lock: asyncio.Lock | None = None
_task: asyncio.Task | None = None
_listener: Callable[[dict[str, Any]], None] | None = None

# 窗口是否显示（app.py 在显示/隐藏时改），隐藏时降频刷新
visible = True


def set_listener(fn: Callable[[dict[str, Any]], None] | None) -> None:
    global _listener
    _listener = fn


def _lk() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def watched_symbols() -> list[str]:
    """滚动条标的（未配置时退回收藏，最多 20 个）。"""
    p = prefs.load()
    strip = prefs.normalize_list(p.get("strip"), config.STRIP_MAX)
    if strip:
        return [x["symbol"] for x in strip]
    watch = prefs.normalize_list(p.get("watch"), config.STRIP_MAX)
    return [x["symbol"] for x in watch]


def track(symbols_: list[str]) -> list[str]:
    """页面请求过的标的也纳入后台刷新（最多 20 个，最近请求优先）。"""
    global _symbols
    merged: list[str] = []
    for s in list(symbols_) + _symbols:
        if s and s not in merged:
            merged.append(s)
    _symbols = merged[: config.STRIP_MAX]
    return _symbols


def get(symbol: str) -> dict[str, Any] | None:
    return _prices.get(symbol)


def last_at(symbol: str) -> float:
    return _at.get(symbol, 0.0)


def updated_at() -> int:
    return int(max(_at.values())) if _at else 0


async def refresh(wanted: list[str] | None = None, why: str = "") -> int:
    targets = [s for s in (wanted or _symbols or watched_symbols()) if s]
    if not targets:
        return 0
    async with _lk():
        try:
            items, errors = await symbols.fetch_quotes(targets)
        except Exception as exc:
            log.info("报价刷新失败: %s", exc)
            return 0
        now = time.time()
        for it in items:
            sym = it.get("symbol")
            if sym:
                _prices[sym] = it
                _at[sym] = now
                _errors.pop(sym, None)
        for err in errors:
            sym = err.get("symbol")
            if sym:
                _errors[sym] = err.get("message") or "无报价"
        if _listener is not None:
            try:
                _listener({"symbols": targets, "first": _prices.get(targets[0]), "count": len(items), "why": why})
            except Exception:
                pass
        return len(items)


async def snapshot(wanted: list[str], max_age: float = config.TICK_MAX_AGE) -> dict[str, Any]:
    wanted = [s for s in wanted if s][: config.STRIP_MAX]
    track(wanted)
    stale = [s for s in wanted if s not in _prices or time.time() - _at.get(s, 0) > max_age]
    if stale:
        # 只补过期/缺失的，不整批重拉，翻页时才不会卡一下
        await refresh(stale, why="snapshot")
    now = time.time()
    return {
        "items": [_prices[s] for s in wanted if s in _prices],
        "missing": [s for s in wanted if s not in _prices],
        "errors": [{"symbol": s, "message": _errors[s]} for s in wanted if s in _errors],
        "updatedAt": updated_at(),
        "age": round(now - max(_at.get(s, 0) for s in wanted), 1) if wanted else 0,
    }


async def _loop() -> None:
    # 启动先让索引预热跑一会儿，别抢首屏带宽
    await asyncio.sleep(3.0)
    while True:
        every = config.TICK_INTERVAL if visible else config.TICK_HIDDEN_INTERVAL
        try:
            targets = _symbols or watched_symbols()
            if targets:
                await refresh(targets, why="loop")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("报价轮询异常: %s", exc)
        await asyncio.sleep(every)


def start() -> None:
    global _task
    if _task is not None and not _task.done():
        return
    try:
        _task = asyncio.create_task(_loop())
        log.info("后台报价刷新已启动")
    except RuntimeError:
        log.debug("事件循环未就绪，跳过报价刷新")
