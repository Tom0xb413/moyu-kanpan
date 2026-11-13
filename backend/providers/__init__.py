"""Provider 注册表。"""
from __future__ import annotations

from .gate import gate_spot
from .gatef import gatef
from .tencent import tencent

REGISTRY = {
    "gf": gatef,
    "gate": gate_spot,
    "tx": tencent,
}

# 降级顺序：合约 -> 同代码现货
FALLBACK = {"gf": ["gate"]}

MARKET_LABEL = {
    "crypto": "加密",
    "us": "美股",
    "index": "指数",
    "metal": "金属",
    "fx": "外汇",
    "commodity": "商品",
    "hk": "港股",
    "cn": "A股",
}

TAG = {"gf": "合约", "gate": "现货", "tx": "真实盘"}

__all__ = ["REGISTRY", "FALLBACK", "MARKET_LABEL", "TAG", "gatef", "gate_spot", "tencent"]
