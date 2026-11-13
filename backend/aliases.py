"""中英文别名：让「英伟达 / apple / 纳指」也能搜到。"""
from __future__ import annotations

# 美股代码 -> 中文名（Gate 合约用 <CODE>_USDT）
STOCK_NAMES = {
    "NVDA": "英伟达",
    "AAPL": "苹果",
    "TSLA": "特斯拉",
    "MSFT": "微软",
    "GOOGL": "谷歌",
    "GOOG": "谷歌",
    "AMZN": "亚马逊",
    "META": "Meta",
    "NFLX": "奈飞",
    "AMD": "超威半导体",
    "INTC": "英特尔",
    "TSM": "台积电",
    "AVGO": "博通",
    "MU": "美光",
    "ORCL": "甲骨文",
    "CRM": "Salesforce",
    "ADBE": "Adobe",
    "COIN": "Coinbase",
    "MSTR": "MicroStrategy",
    "PLTR": "Palantir",
    "SPY": "标普500ETF",
    "QQQ": "纳指100ETF",
    "SQQQ": "三倍做空纳指",
    "IWM": "罗素2000ETF",
    "BABA": "阿里巴巴",
    "PDD": "拼多多",
    "JD": "京东",
    "NIO": "蔚来",
    "XPEV": "小鹏",
    "LI": "理想",
    "BIDU": "百度",
    "CRCL": "Circle",
    "HOOD": "Robinhood",
    "SMCI": "超微电脑",
    "ARM": "Arm",
    "QCOM": "高通",
    "MRVL": "迈威尔",
    "UBER": "优步",
    "DIS": "迪士尼",
    "BA": "波音",
    "JPM": "摩根大通",
    "GS": "高盛",
    "V": "Visa",
    "MA": "万事达",
    "WMT": "沃尔玛",
    "KO": "可口可乐",
    "PEP": "百事",
}

# 英文关键词 -> 代码
EN_ALIAS = {
    "apple": "AAPL",
    "iphone": "AAPL",
    "nvidia": "NVDA",
    "tesla": "TSLA",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "meta": "META",
    "facebook": "META",
    "netflix": "NFLX",
    "broadcom": "AVGO",
    "tsmc": "TSM",
    "coinbase": "COIN",
    "microstrategy": "MSTR",
    "palantir": "PLTR",
    "alibaba": "BABA",
    "pinduoduo": "PDD",
    "baidu": "BIDU",
}

# 精确别名 -> 直接 symbol（指数/港股/黄金）
SYMBOL_ALIAS = {
    "纳指": "tx:usIXIC",
    "纳斯达克": "tx:usIXIC",
    "标普": "tx:usINX",
    "标普500": "tx:usINX",
    "道指": "tx:usDJI",
    "道琼斯": "tx:usDJI",
    "恒指": "tx:hkHSI",
    "恒生指数": "tx:hkHSI",
    "上证": "tx:sh000001",
    "上证指数": "tx:sh000001",
    "黄金": "gf:PAXG_USDT",
    "原油": "gf:CL_USDT",
    "美元指数": "gf:DXY_USDT",
}

# 中文名 -> 代码（反查）
CN_ALIAS = {v: k for k, v in STOCK_NAMES.items()}


def lookup_ticker(q: str) -> str:
    q = (q or "").strip()
    if not q:
        return ""
    low = q.lower()
    if low in EN_ALIAS:
        return EN_ALIAS[low]
    for k, v in EN_ALIAS.items():
        if k.startswith(low) and len(low) >= 3:
            return v
    if q in CN_ALIAS:
        return CN_ALIAS[q]
    for k, v in CN_ALIAS.items():
        if k.startswith(q) and len(q) >= 2:
            return v
    up = q.upper()
    if up in STOCK_NAMES:
        return up
    return ""


def lookup_symbol(q: str) -> str:
    q = (q or "").strip()
    if q in SYMBOL_ALIAS:
        return SYMBOL_ALIAS[q]
    for k, v in SYMBOL_ALIAS.items():
        if q and k.startswith(q) and len(q) >= 2:
            return v
    return ""


def display_name(code: str) -> str:
    base = code.split("_")[0].upper()
    return STOCK_NAMES.get(base, "")
