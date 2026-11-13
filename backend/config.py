"""全局配置：路径、周期、TTL、限速、日志。"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "MoYuKanPan"
APP_TITLE = "摸鱼看盘"
VERSION = "1.3.2"

PERIODS = ["15m", "1h", "4h", "8h", "1d", "1w"]
PERIOD_LABEL = {"15m": "15M", "1h": "1H", "4h": "4H", "8h": "8H", "1d": "1D", "1w": "1W"}
PERIOD_SEC = {"15m": 900, "1h": 3600, "4h": 14400, "8h": 28800, "1d": 86400, "1w": 604800}

# 提供给上游的 interval（Gate 的周线是 7d）
GATE_INTERVAL = {"15m": "15m", "1h": "1h", "4h": "4h", "8h": "8h", "1d": "1d", "1w": "7d"}

TTL = {
    "15m": 40,
    "1h": 90,
    "4h": 180,
    "8h": 300,
    "1d": 600,
    "1w": 1800,
    "quote": 5,
    "index": 6 * 3600,
}

TIMEOUT_TOTAL = 9.0
TIMEOUT_CONNECT = 4.5
RETRY = 2
BACKOFF = (0.35, 0.9)
MIN_INTERVAL_PER_HOST = 0.2
MAX_CONCURRENCY = 6
DEFAULT_LIMIT = 800
MAX_BARS = 1000

# 历史分页：向左滚动时每次往回拉多少根
PAGE_BARS = 400

# K 线：单次请求短超时、不重试，失败立刻回落本地，把重试交给下一轮刷新
KLINE_TIMEOUT = 6.0
KLINE_RETRY = 0

# 本地 K 线库（同时是「最多能往回翻多深」的上限）
STORE_MAX_FILES = 240
STORE_MAX_BARS = 4000

# 收藏标的后台预热
WARM_DELAY = 6.0
WARM_INTERVAL = 0.30

# 托盘 / 滚动条（横向 5 列、竖向 5 行）
STRIP_W = 560
STRIP_H = 32
STRIP_V_W = 220
STRIP_V_H = 150
STRIP_PER_PAGE = 5
STRIP_MAX = 20
STRIP_PAGE_SEC = 6.0
STRIP_ALPHA = 0.9
STRIP_MIN_W = 180
STRIP_MIN_H = 26

# 后台报价刷新（托盘/滚动条共用）
TICK_INTERVAL = 4.0
TICK_HIDDEN_INTERVAL = 15.0
TICK_MAX_AGE = 2.5

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
)

GATE_BASE = "https://api.gateio.ws/api/v4"
TX_QUOTE = "https://qt.gtimg.cn/q="
TX_SMARTBOX = "https://smartbox.gtimg.cn/s3/"
TX_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/{ep}/get"

HDR_GATE = {"Accept": "application/json", "User-Agent": UA}
HDR_TX = {
    "Referer": "https://gu.qq.com/",
    "User-Agent": UA,
    "Accept": "*/*",
}


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
FRONTEND_DIR = BASE_DIR / "frontend"
VENDOR_DIR = BASE_DIR / "vendor"

DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
CACHE_DIR = DATA_DIR / "cache"
BARS_DIR = DATA_DIR / "bars"
PREFS_FILE = DATA_DIR / "prefs.json"
LOG_FILE = DATA_DIR / "app.log"

_cache_ready = False


def ensure_dirs() -> None:
    global _cache_ready
    if _cache_ready:
        return
    for d in (DATA_DIR, CACHE_DIR, BARS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    _cache_ready = True


def setups_logging(debug: bool = False) -> logging.Logger:
    ensure_dirs()
    root = logging.getLogger()
    if root.handlers:
        return root
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s", "%H:%M:%S")
    fh = RotatingFileHandler(LOG_FILE, maxBytes=1_500_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    return root
