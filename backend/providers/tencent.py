"""腾讯行情（副源·真实盘）：美股/港股/A股/指数 日线与报价，及中文搜索。"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .. import config
from ..models import Bar, Quote, clean_bars, to_utc_day, to_utc_week
from ..net import ProviderError, net
from .base import BaseProvider

log = logging.getLogger("tx")

V_RE = re.compile(r'v_([A-Za-z0-9_.]+)="([^"]*)"')

_TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]*$")


def _market(code: str) -> str:
    if code.startswith("us"):
        return "us"
    if code.startswith("hk"):
        return "hk"
    return "cn"


def normalize_code(code: str) -> str:
    """把裸代码统一成腾讯接口认识的形式。

    smartbox 把市场放在独立字段（``sh~588200~科创芯片ETF嘉实``），早期版本拼
    symbol 时把它丢了，生成 ``tx:588200``；而腾讯 K 线 / 报价接口只认
    ``sh588200``，裸码会返回 ``param error`` 并最终报「无可用数据源」。
    这里同时兜住历史收藏 / 近期里已保存的裸代码。

    - 已有市场前缀：原样保留（美股后缀大小写敏感，只大写后缀，不动前缀）
    - 6 位数字：沪市 5/6/9 开头，深市 0/1/2/3 开头，北交所 4/8 开头
    - 1~5 位数字：港股，左补零到 5 位
    - 字母开头：美股
    """
    c = (code or "").strip()
    if not c:
        return c
    low = c.lower()
    if len(c) > 2 and low[:2] in ("sh", "sz", "bj", "hk", "us"):
        return ("us" + c[2:].upper()) if low.startswith("us") else (low[:2] + c[2:])
    if len(c) == 6 and c.isdigit():
        head = c[0]
        if head in "0123":
            return "sz" + c
        if head in "48":
            return "bj" + c
        return "sh" + c
    if c.isdigit() and 1 <= len(c) <= 5:
        return "hk" + c.zfill(5)
    if _TICKER_RE.match(c):
        return "us" + c.upper()
    return c


class Tencent(BaseProvider):
    name = "tx"
    label = "真实盘"
    periods = ["1d", "1w"]

    # smartbox 搜索结果的中文名（进程内缓存）。
    # 注意：providers 包把本模块名导出成了实例，所以缓存必须挂在类上，
    # 其它模块拿到的 tencent 其实是 Tencent() 实例。
    NAMES: dict[str, str] = {}
    normalize_code = staticmethod(normalize_code)

    def supports(self, period: str) -> bool:
        return period in ("1d", "1w")

    @staticmethod
    def _ep_for(code: str) -> str:
        return {"us": "usfqkline", "hk": "hkfqkline", "cn": "fqkline"}[_market(code)]

    @staticmethod
    def _parse_rows(rows: list[list[Any]] | None, p: str, code: str) -> list[Bar]:
        if not rows:
            raise ProviderError(f"{code} 无 K 线数据", "EMPTY", 404)
        bars: list[Bar] = []
        for r in rows:
            try:
                # [日期, 开, 收, 高, 低, 量]
                t = to_utc_week(str(r[0])) if p == "week" else to_utc_day(str(r[0]))
                bars.append(
                    Bar(
                        t,
                        float(r[1]),
                        float(r[3]),
                        float(r[4]),
                        float(r[2]),
                        float(r[5]),
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        bars = clean_bars(bars)
        if not bars:
            raise ProviderError(f"{code} K 线解析为空", "EMPTY", 404)
        return bars

    async def _kline_rows(self, code: str, param_mid: str, p: str,
                          allow_empty: bool = False) -> list[list[Any]]:
        url = config.TX_KLINE.format(ep=self._ep_for(code)) + f"?param={code},{p},{param_mid}"
        text = await net.get_text(
            url,
            provider="tx",
            headers=config.HDR_TX,
            timeout=config.KLINE_TIMEOUT,
            retry=config.KLINE_RETRY,
        )
        try:
            js = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError("腾讯 K 线返回非 JSON", "BAD_JSON", 502) from exc
        if js.get("code") != 0:
            raise ProviderError(f"腾讯 K 线错误：{js.get('msg')}", "UPSTREAM", 502)
        data = js.get("data")
        # 裸代码（缺 sh/sz/hk 前缀）时腾讯返回 data=[] 且 msg="param error"
        if not isinstance(data, dict) or not data:
            if allow_empty:
                return []                  # 往前翻到上市之前，正常结束
            raise ProviderError(
                f"{code} 无 K 线数据：{js.get('msg') or '代码不被识别'}", "EMPTY", 404
            )
        node = data.get(code)
        if node is None:                       # 个别市场返回的 key 大小写不同
            node = next((v for k, v in data.items() if k.lower() == code.lower()), None)
        if not node:
            raise ProviderError(f"{code} 无 K 线数据", "EMPTY", 404)
        for key in (f"qfq{p}", p, f"qfq{p[0]}"):
            if isinstance(node.get(key), list) and node[key]:
                return node[key]
        if allow_empty:
            return []
        raise ProviderError(f"{code} 无 K 线数据", "EMPTY", 404)

    async def klines(self, code: str, period: str, limit: int) -> list[Bar]:
        c = normalize_code(code)
        p = "day" if period == "1d" else "week"
        n = max(60, min(int(limit or 320), 640))
        rows = await self._kline_rows(c, f",,{n},qfq", p)
        return self._parse_rows(rows, p, c)

    async def klines_before(self, code: str, period: str, before: int, count: int) -> list[Bar]:
        """腾讯：end=YYYY-MM-DD 指定最后一根，count 往前取（接口上限 640）。"""
        from datetime import datetime, timezone

        c = normalize_code(code)
        p = "day" if period == "1d" else "week"
        n = max(60, min(int(count or config.PAGE_BARS), 640))
        end_day = datetime.fromtimestamp(max(0, int(before) - 1), tz=timezone.utc).date()
        rows = await self._kline_rows(c, f",{end_day.isoformat()},{n},qfq", p, allow_empty=True)
        if not rows:
            return []          # 已经翻到上市之前，正常结束
        bars = self._parse_rows(rows, p, c)
        return [b for b in bars if b.t < int(before)][-n:]

    async def quote_many(self, codes: list[str]) -> dict[str, Quote]:
        if not codes:
            return {}
        # 实时报价接口不认 .OQ/.N 后缀，需去掉；K 线接口才需要后缀
        # 同时把历史遗留的裸代码（tx:588200）补成 sh588200 之类的合法形式
        req: list[str] = []
        back: dict[str, str] = {}
        for c in codes:
            n = normalize_code(c)
            r = n.split(".")[0] if (n.startswith("us") and "." in n) else n
            back[c] = r
            if r not in req:
                req.append(r)

        url = config.TX_QUOTE + ",".join(req)
        text = await net.get_text(url, provider="tx", headers=config.HDR_TX, encoding="gbk")

        raw: dict[str, Quote] = {}
        for code, payload in V_RE.findall(text):
            q = self._parse_quote(code, payload)
            if not q:
                continue
            raw[code] = q
            raw[code.lower()] = q
            if code.startswith("us"):
                parts = payload.split("~")
                if len(parts) > 2 and parts[2]:
                    raw.setdefault("us" + parts[2].upper(), q)

        out: dict[str, Quote] = {}
        for c, r in back.items():
            q = raw.get(r) or raw.get(r.lower())
            if q:
                out[c] = q
        return out

    async def quote(self, code: str) -> Quote:
        got = await self.quote_many([code])
        if code in got:
            return got[code]
        for k, v in got.items():
            if k.lower() == code.lower():
                return v
        raise ProviderError(f"{code} 无报价", "NOT_FOUND", 404)

    @staticmethod
    def _parse_quote(code: str, payload: str) -> Quote | None:
        if not payload:
            return None
        if code.startswith("hf_"):
            f = payload.split(",")
            if len(f) < 8:
                return None
            try:
                last = float(f[0])
                chg = float(f[1])
                high = float(f[4])
                low = float(f[5])
            except ValueError:
                return None
            return Quote(
                symbol=f"tx:{code}",
                name=f[-1] if f else code,
                last=last,
                chg_pct=chg,
                high=high,
                low=low,
                volume=0.0,
                ts=int(time.time()),
                source="tencent",
            )

        f = payload.split("~")
        if len(f) < 35:
            return None
        try:
            last = float(f[3])
            prev = float(f[4])
            high = float(f[33]) or last
            low = float(f[34]) or last
            chg = float(f[32])
            vol = float(f[6] or 0)
        except (IndexError, ValueError):
            return None
        if last <= 0:
            return None
        return Quote(
            symbol=f"tx:{code}",
            name=f[1],
            last=last,
            chg_pct=chg,
            high=high,
            low=low,
            volume=vol,
            ts=int(time.time()),
            source="tencent",
            extra={"prevClose": prev, "time": f[30] if len(f) > 30 else ""},
        )

    async def catalog(self) -> list[dict[str, Any]]:
        return []

    async def search(self, q: str, limit: int = 10) -> list[dict[str, Any]]:
        from urllib.parse import quote

        url = f"{config.TX_SMARTBOX}?q={quote(q)}&t=all&c=1"
        try:
            text = await net.get_text(url, provider="tx", headers=config.HDR_TX, retry=1)
        except ProviderError:
            return []
        m = re.search(r'v_hint="([^"]*)"', text)
        if not m:
            return []
        try:
            raw = json.loads('"' + m.group(1).replace('"', '\\"') + '"')
        except json.JSONDecodeError:
            raw = m.group(1)
        out = []
        # jj（场外基金）只有净值没有 K 线，放进结果会点开就报「无可用数据源」
        mkt_label = {"us": "美股", "hk": "港股", "sh": "A股", "sz": "A股", "bj": "A股"}
        for item in raw.split("^"):
            parts = item.split("~")
            if len(parts) < 5:
                continue
            mkt, code, name, pinyin, kind = parts[0], parts[1], parts[2], parts[3], parts[4]
            if mkt not in mkt_label:
                continue
            if mkt == "us":
                # smartbox 返回裸代码（aapl.oq / tcehy.ps）；已带 us 前缀的则先去掉
                bare = code[2:] if (code.startswith("us") and len(code) > 2 and code[2].isupper()) else code
                code = "us" + bare.upper()
            elif not code.lower().startswith(mkt):
                # 关键：市场是独立字段，必须拼进代码，否则 K 线/报价接口不认
                code = mkt + code
            if kind == "ZS":
                market = "index"
            elif mkt == "us":
                market = "us"
            elif mkt == "hk":
                market = "hk"
            else:
                market = "cn"
            Tencent.NAMES[f"tx:{code}"] = name or code
            out.append(
                {
                    "symbol": f"tx:{code}",
                    "code": code,
                    "base": code.upper(),
                    "market": market,
                    "name": name or code,
                    "provider": "tx",
                    "periods": ["1d", "1w"],
                    "pinyin": pinyin,
                }
            )
            if len(out) >= limit:
                break
        return out


tencent = Tencent()
