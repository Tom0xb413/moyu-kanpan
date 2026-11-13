"""HTTP 客户端：并发闸门、按 host 限速、重试退避、GBK 解码、统计。"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from . import config

log = logging.getLogger("net")


class ProviderError(Exception):
    def __init__(self, message: str, code: str = "PROVIDER_DOWN", status: int = 502):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


class Net:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._sem = asyncio.Semaphore(config.MAX_CONCURRENCY)
        self._host_last: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self.stats: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        if self._session and not self._session.closed:
            return
        timeout = aiohttp.ClientTimeout(
            total=config.TIMEOUT_TOTAL,
            connect=config.TIMEOUT_CONNECT,
            sock_read=config.TIMEOUT_TOTAL,
        )
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            trust_env=False,  # 关键：绕开系统代理 127.0.0.1:7890
            connector=aiohttp.TCPConnector(limit=16, ttl_dns_cache=300, enable_cleanup_closed=True),
            auto_decompress=True,
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ---------- 限速 ----------
    async def _pace(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._host_last.get(host, 0.0) + config.MIN_INTERVAL_PER_HOST - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    def _note(self, provider: str, ok: bool, ms: float, err: str = "") -> None:
        s = self.stats.setdefault(provider, {"ok": 0, "err": 0, "ms": 0.0, "n": 0, "last": ""})
        s["n"] += 1
        s["ms"] = round(s["ms"] * 0.8 + ms * 0.2, 1)
        if ok:
            s["ok"] += 1
        else:
            s["err"] += 1
            s["last"] = err[:160]

    # ---------- 主请求 ----------
    async def get_text(
        self,
        url: str,
        *,
        provider: str,
        headers: dict[str, str] | None = None,
        encoding: str = "utf-8",
        retry: int | None = None,
        timeout: float | None = None,
    ) -> str:
        await self.start()
        assert self._session is not None
        host = urlsplit(url).netloc
        tries = (config.RETRY if retry is None else retry) + 1
        last_err: Exception | None = None
        req_kw: dict[str, Any] = {}
        if timeout:
            req_kw["timeout"] = aiohttp.ClientTimeout(
                total=timeout,
                connect=min(config.TIMEOUT_CONNECT, timeout),
                sock_read=timeout,
            )

        for attempt in range(tries):
            started = time.monotonic()
            try:
                async with self._sem:
                    await self._pace(host)
                    async with self._session.get(url, headers=headers or {}, **req_kw) as resp:
                        body = await resp.read()
                        ms = (time.monotonic() - started) * 1000
                        if resp.status == 429:
                            ra = float(resp.headers.get("Retry-After") or 1.0)
                            self._note(provider, False, ms, "429")
                            if attempt == 0:
                                await asyncio.sleep(min(2.0, max(0.3, ra)))
                                continue
                            raise ProviderError("上游限流(429)，稍后重试", "RATE_LIMIT", 429)
                        if resp.status == 404:
                            self._note(provider, False, ms, f"404 {url}")
                            raise ProviderError("标的不存在或已下线", "NOT_FOUND", 404)
                        if resp.status >= 400:
                            self._note(provider, False, ms, f"{resp.status}")
                            raise ProviderError(f"上游返回 {resp.status}", "UPSTREAM", 502)
                        text = body.decode(encoding, "replace")
                        self._note(provider, True, ms)
                        return text
            except ProviderError:
                raise
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                ms = (time.monotonic() - started) * 1000
                last_err = exc
                self._note(provider, False, ms, f"{type(exc).__name__}: {exc}")
                if attempt < tries - 1:
                    base = config.BACKOFF[min(attempt, len(config.BACKOFF) - 1)]
                    await asyncio.sleep(base + random.uniform(0, 0.15))
                    continue
                raise ProviderError(f"连接失败：{type(exc).__name__}", "NETWORK", 502) from exc
            except UnicodeDecodeError as exc:  # 极少见，解码已带 replace
                raise ProviderError("响应解码失败", "DECODE", 502) from exc

        raise ProviderError(f"请求失败：{last_err}", "NETWORK", 502)

    async def get_json(
        self,
        url: str,
        *,
        provider: str,
        headers: dict[str, str] | None = None,
        retry: int | None = None,
        timeout: float | None = None,
    ) -> Any:
        import json

        text = await self.get_text(
            url, provider=provider, headers=headers, retry=retry, timeout=timeout
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError("上游返回非 JSON", "BAD_JSON", 502) from exc


net = Net()
