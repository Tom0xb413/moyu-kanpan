"""prefs.json 原子读写（private_mode 下 localStorage 不可靠，故落盘）。"""
from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any

from . import config

RECENT_MAX = 10
WATCH_MAX = 30
STRIP_MAX = 20

DEFAULTS: dict[str, Any] = {
    "w": 520,
    "h": 400,
    "x": None,
    "y": None,
    "on_top": True,
    "alpha": 1.0,
    "transparent": False,
    "symbol": "gf:BTC_USDT",
    "period": "15m",
    "vol": True,
    "ema": {"20": True, "50": True, "100": True, "200": True},
    "recent": [],
    "watch": [],
    # 滚动条（收起）模式
    "mode": "full",
    "strip": [],
    "strip_alpha": 0.9,
    "strip_dir": "h",          # h=横向 5 列 / v=竖向 5 行
    # 点关闭按钮 / 窗口 X 时的行为
    "exit_ask": True,          # False 时不再问询，直接按 exit_action 执行
    "exit_action": "tray",     # quit=直接退出 / tray=隐藏主窗、保留实时价位条与托盘
    "hotkey": {"mods": 3, "vk": 72, "label": "Ctrl+Alt+H"},
    "first_run": True,
}

_lock = threading.RLock()  # 可重入：save() 内部会调 load()
_state: dict[str, Any] | None = None


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if k in ("ema",) and isinstance(v, dict):
            out.setdefault("ema", {}).update(v)
        else:
            out[k] = v
    return out


def _norm_item(it: Any) -> dict[str, str] | None:
    """兼容旧版字符串数组与新对象数组。"""
    if isinstance(it, str):
        s = it.strip()
        return {"symbol": s, "name": "", "label": "", "tag": ""} if s else None
    if isinstance(it, dict):
        s = str(it.get("symbol") or "").strip()
        if not s:
            return None
        return {
            "symbol": s,
            "name": str(it.get("name") or ""),
            "label": str(it.get("label") or it.get("marketLabel") or ""),
            "tag": str(it.get("tag") or ""),
        }
    return None


def normalize_list(v: Any, limit: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for it in (v or []):
        d = _norm_item(it)
        if not d or d["symbol"] in seen:
            continue
        seen.add(d["symbol"])
        out.append(d)
        if len(out) >= limit:
            break
    return out


def _normalize_state(st: dict[str, Any]) -> dict[str, Any]:
    st["recent"] = normalize_list(st.get("recent"), RECENT_MAX)
    st["watch"] = normalize_list(st.get("watch"), WATCH_MAX)
    st["strip"] = normalize_list(st.get("strip"), STRIP_MAX)
    if st.get("mode") not in ("full", "strip"):
        st["mode"] = "full"
    if st.get("strip_dir") not in ("h", "v"):
        st["strip_dir"] = "h"
    if st.get("exit_action") not in ("quit", "tray"):
        st["exit_action"] = "tray"
    st["exit_ask"] = bool(st.get("exit_ask", True))
    return st


def load() -> dict[str, Any]:
    global _state
    with _lock:
        if _state is not None:
            return copy.deepcopy(_state)
        data: dict[str, Any] = {}
        try:
            if config.PREFS_FILE.exists():
                data = json.loads(config.PREFS_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        _state = _merge(DEFAULTS, data if isinstance(data, dict) else {})
        _normalize_state(_state)
        return copy.deepcopy(_state)


def save(patch: dict[str, Any]) -> dict[str, Any]:
    global _state
    with _lock:
        cur = _state if _state is not None else load()
        cur = _merge(cur, patch)
        _normalize_state(cur)
        _state = cur
        try:
            config.ensure_dirs()
            tmp = config.PREFS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, config.PREFS_FILE)
        except Exception:
            pass
        return copy.deepcopy(cur)


def push_recent(symbol: str, name: str = "", label: str = "", tag: str = "",
                limit: int = RECENT_MAX) -> None:
    p = load()
    it = {"symbol": symbol, "name": name, "label": label, "tag": tag}
    recent = [it] + [x for x in p.get("recent", []) if x.get("symbol") != symbol]
    save({"recent": recent[:limit], "symbol": symbol})
