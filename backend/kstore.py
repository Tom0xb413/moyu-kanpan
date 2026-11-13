"""本地 K 线库：按 (symbol, period) 落盘，增量合并、原子写、LRU 清理。

设计要点：
- at 只表示「最后一次成功联网」的时间，不用文件 mtime 代替。
- 同一根未收盘 K 线会被新数据覆盖（dict[t] = bar），合并后严格升序。
- 写盘失败只记日志，内存镜像不更新，避免出现「内存是新的、磁盘是旧的」的假新鲜。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import config

log = logging.getLogger("kstore")

VERSION = 1
MAX_MEMO = 600

_mem: dict[str, dict[str, Any]] = {}
_lock = threading.RLock()


def _k(symbol: str, period: str) -> str:
    return symbol + "|" + period


def path_for(symbol: str, period: str) -> Path:
    tag = quote(symbol, safe="")
    h = hashlib.sha1(symbol.encode("utf-8")).hexdigest()[:6]
    return config.BARS_DIR / f"{tag}__{period}__{h}.json"


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def sanitize(rows: Any) -> list[list[float]]:
    """6 列校验 + 按 t 去重（后到覆盖先到）+ 升序。"""
    out: dict[int, list[float]] = {}
    if not isinstance(rows, list):
        return []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 6:
            continue
        t = _f(r[0])
        vals = [_f(x) for x in r[1:6]]
        if t is None or any(v is None for v in vals):
            continue
        o, h, l, c, v = vals  # type: ignore[misc]
        if min(o, h, l, c) <= 0:
            continue
        ti = int(t)
        out[ti] = [ti, o, h, l, c, v]
    return [out[t] for t in sorted(out)]


def load(symbol: str, period: str) -> dict[str, Any] | None:
    key = _k(symbol, period)
    with _lock:
        hit = _mem.get(key)
    if hit is not None:
        return hit

    p = path_for(symbol, period)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # 截断/半写/编码错
        log.warning("本地库损坏 %s: %s", p.name, exc)
        try:
            p.replace(p.with_name(p.name + ".bad"))
        except Exception:
            pass
        return None

    if not isinstance(raw, dict):
        return None
    bars = sanitize(raw.get("bars"))
    if not bars:
        return None
    payload: dict[str, Any] = {
        "symbol": symbol,
        "period": period,
        "provider": str(raw.get("provider") or ""),
        "at": _f(raw.get("at")) or 0.0,
        "bars": bars,
    }
    with _lock:
        if len(_mem) >= MAX_MEMO:
            _mem.pop(next(iter(_mem)), None)
        _mem[key] = payload
    return payload


def merge(old: list[list[float]], new: list[list[float]],
          keep: int = config.STORE_MAX_BARS) -> list[list[float]]:
    d: dict[int, list[float]] = {int(b[0]): b for b in (old or [])}
    for b in (new or []):
        d[int(b[0])] = b
    return [d[t] for t in sorted(d)][-keep:]


def needs_write(old: dict[str, Any] | None, bars: list[list[float]], now: float) -> bool:
    """末柱没变且 6 小时内写过，就不重复落盘；否则写。"""
    if not old or not old.get("bars"):
        return True
    ob = old["bars"]
    if len(ob) != len(bars) or ob[-1][0] != bars[-1][0]:
        return True
    if ob[-1][4] != bars[-1][4] or ob[-1][5] != bars[-1][5]:
        return True
    return now - float(old.get("at") or 0) > 6 * 3600


def touch(symbol: str, period: str, at: float | None = None) -> None:
    with _lock:
        p = _mem.get(_k(symbol, period))
        if p is not None:
            p["at"] = float(at or time.time())


def save(symbol: str, period: str, provider: str,
         bars: list[list[float]], at: float | None = None) -> bool:
    payload = {
        "version": VERSION,
        "symbol": symbol,
        "period": period,
        "provider": provider,
        "at": float(time.time() if at is None else at),
        "bars": bars,
    }
    try:
        config.ensure_dirs()
        p = path_for(symbol, period)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{time.time_ns()}.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, p)
        with _lock:
            _mem[_k(symbol, period)] = payload
        return True
    except Exception as exc:
        log.warning("本地库写入失败 %s %s: %s", symbol, period, exc)
        return False


def extend(symbol: str, period: str, provider: str, page: list[list[float]],
           at: float | None = None, keep: int | None = None) -> list[list[float]]:
    """把「更早的一页」并进本地库（不改变 at 的新鲜度语义）。

    at 传 None 时沿用原有值；原本没有库则记 0（表示尾部尚未联网校准）。
    返回合并后的全量（最多 keep 根）。
    """
    cap = int(keep or config.STORE_MAX_BARS)
    with _lock:
        st = load(symbol, period)
        old = st["bars"] if st else []
        merged = merge(old, page, keep=cap)
        prov = provider or (st.get("provider") if st else "") or ""
        keep_at = at if at is not None else (float(st.get("at") or 0.0) if st else 0.0)
        save(symbol, period, prov, merged, at=keep_at)
        return merged


def depth(symbol: str, period: str) -> int:
    st = load(symbol, period)
    return len(st["bars"]) if st else 0


def stats() -> dict[str, Any]:
    files = list(config.BARS_DIR.glob("*.json")) if config.BARS_DIR.exists() else []
    total = 0
    oldest = time.time()
    for f in files:
        try:
            st = f.stat()
            total += st.st_size
            oldest = min(oldest, st.st_mtime)
        except OSError:
            pass
    return {
        "files": len(files),
        "kb": round(total / 1024, 1),
        "memo": len(_mem),
        "oldestH": round((time.time() - oldest) / 3600, 1) if files else 0,
    }


def prune(protect: list[str] | None = None, keep: int = config.STORE_MAX_FILES) -> int:
    """按 mtime LRU 清理；收藏标的的全部周期文件永不删。"""
    if not config.BARS_DIR.exists():
        return 0
    guarded = {
        path_for(s, per).name
        for s in (protect or [])
        for per in config.PERIODS
    }
    rows: list[tuple[float, Path]] = []
    for f in config.BARS_DIR.glob("*.json"):
        try:
            rows.append((f.stat().st_mtime, f))
        except OSError:
            continue
    rows.sort(key=lambda x: x[0], reverse=True)

    removed = 0
    for i, (_, f) in enumerate(rows):
        if i < keep or f.name in guarded:
            continue
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass

    now = time.time()
    for f in config.BARS_DIR.glob("*.tmp"):
        try:
            if now - f.stat().st_mtime > 86400:
                f.unlink()
        except OSError:
            pass
    for f in config.BARS_DIR.glob("*.bad"):
        try:
            if now - f.stat().st_mtime > 7 * 86400:
                f.unlink()
        except OSError:
            pass
    return removed
