"""内存 TTL 缓存 + single-flight + 磁盘快照（离线兜底）。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import config

log = logging.getLogger("cache")


@dataclass
class Entry:
    at: float
    value: Any
    source: str = ""

    def age(self) -> float:
        return time.time() - self.at


class TTLCache:
    def __init__(self) -> None:
        self._d: dict[str, Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def peek(self, key: str) -> Entry | None:
        return self._d.get(key)

    def fresh(self, key: str, ttl: float) -> Entry | None:
        e = self._d.get(key)
        if e and e.age() <= ttl:
            return e
        return None

    def put(self, key: str, value: Any, source: str = "") -> Entry:
        e = Entry(time.time(), value, source)
        self._d[key] = e
        if len(self._d) > 400:
            oldest = sorted(self._d.items(), key=lambda kv: kv[1].at)[:80]
            for k, _ in oldest:
                self._d.pop(k, None)
        return e

    async def run(
        self,
        key: str,
        ttl: float,
        loader: Callable[[], Awaitable[Any]],
        *,
        force: bool = False,
        stale_ok: bool = False,
    ) -> tuple[Any, Entry, bool]:
        """返回 (值, Entry, 是否 stale)。同 key 并发只打一次上游。"""
        if not force:
            hit = self.fresh(key, ttl)
            if hit:
                return hit.value, hit, False
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not force:
                hit = self.fresh(key, ttl)
                if hit:
                    return hit.value, hit, False
            try:
                value = await loader()
                e = self.put(key, value)
                return value, e, False
            except Exception as exc:
                old = self._d.get(key)
                if stale_ok and old is not None:
                    log.warning("降级到内存旧值 %s: %s", key, exc)
                    return old.value, old, True
                snap = load_snapshot(key)
                if stale_ok and snap is not None:
                    e = Entry(snap["at"], snap["payload"], "snapshot")
                    self._d[key] = e
                    log.warning("降级到磁盘快照 %s", key)
                    return snap["payload"], e, True
                raise


def _snap_path(key: str) -> Path:
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return config.CACHE_DIR / f"{h}.json"


def save_snapshot(key: str, payload: Any) -> None:
    try:
        config.ensure_dirs()
        p = _snap_path(key)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"key": key, "at": time.time(), "payload": payload}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, p)
    except Exception as exc:  # 快照失败不影响主流程
        log.debug("写快照失败 %s: %s", key, exc)


def load_snapshot(key: str) -> dict | None:
    try:
        p = _snap_path(key)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("at", 0) > 14 * 86400:
            return None
        return data
    except Exception:
        return None


def prune_snapshots(limit: int = 400) -> None:
    try:
        files = sorted(config.CACHE_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[limit:]:
            f.unlink(missing_ok=True)
    except Exception:
        pass


cache = TTLCache()
