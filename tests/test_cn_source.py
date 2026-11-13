"""腾讯真实盘（A股/港股/美股）数据源回归测试。

直接运行 `python tests/test_cn_source.py`，不依赖 pytest，走真实网络。
测试会把本地 K 线库指到临时目录，不污染真实缓存/偏好。

背景：smartbox 把市场放在独立字段（sh~588200~科创芯片ETF嘉实），早期版本
拼 symbol 时丢了它，生成 tx:588200，腾讯 K 线接口只认 sh588200，于是报
「无可用数据源」。本用例覆盖 搜索 -> K线 -> 报价 -> 历史分页 全链路。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, kstore, search, symbols  # noqa: E402
from backend.net import ProviderError, net  # noqa: E402
from backend.providers import tencent  # noqa: E402

OK: list[str] = []
FAIL: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def record(label: str, detail: str = "") -> None:
    OK.append(label)
    print(f"  [OK] {label}{(' · ' + detail) if detail else ''}")


# ---------------- 1. 纯函数：代码归一化 ----------------

def test_normalize() -> None:
    cases = {
        "588200": "sh588200",       # 科创板 ETF
        "600519": "sh600519",       # 沪市主板
        "000001": "sz000001",       # 深市（裸码只能按规则猜，搜索结果会带前缀）
        "300750": "sz300750",       # 创业板
        "159915": "sz159915",       # 深市 ETF
        "510300": "sh510300",       # 沪市 ETF
        "430047": "bj430047",       # 北交所
        "00700": "hk00700",         # 港股
        "700": "hk00700",
        "AAPL": "usAAPL",
        "aapl.oq": "usAAPL.OQ",
        "sh588200": "sh588200",     # 已有前缀不能破坏
        "sh000001": "sh000001",     # 上证指数
        "sz399001": "sz399001",
        "hkHSI": "hkHSI",           # 精选指数，大小写敏感
        "hkHSTECH": "hkHSTECH",
        "usIXIC": "usIXIC",
        "usINX": "usINX",
        "usAAPL.OQ": "usAAPL.OQ",
        "usaapl.oq": "usAAPL.OQ",   # 小写也纠正
    }
    for raw, want in cases.items():
        got = tencent.normalize_code(raw)
        check(got == want, f"normalize_code({raw!r}) = {got!r}，期望 {want!r}")
    record("normalize_code 代码补全/幂等", f"{len(cases)} 例")


# ---------------- 2. 搜索结果必须带市场前缀 ----------------

async def test_search() -> None:
    print("\n[搜索] smartbox 结果")
    plan = [
        ("588200", "tx:sh588200", "cn"),
        ("600519", "tx:sh600519", "cn"),
        ("000001", "tx:sh000001", "index"),
        ("510300", "tx:sh510300", "cn"),
        ("比亚迪", "tx:sz002594", "cn"),
        ("00700", "tx:hk00700", "hk"),
        ("aapl", "tx:usAAPL.OQ", "us"),
    ]
    for q, want_sym, want_market in plan:
        items = await tencent.search(q, limit=8)
        hit = next((i for i in items if i["symbol"] == want_sym), None)
        check(hit is not None, f"搜索 {q!r} 未得到 {want_sym}：{[i['symbol'] for i in items]}")
        check(not any(i["symbol"].startswith("tx:jj") for i in items),
              f"搜索 {q!r} 混入了无 K 线的场外基金")
        check(all(not i["code"].isdigit() for i in items),
              f"搜索 {q!r} 仍有裸代码：{[i['code'] for i in items]}")
        record(f"搜索 {q} -> {hit['symbol']}", f"{hit['name']} / {want_market}")

    # 完整 Catalog 链路（本地索引 + smartbox 补充）
    cat = search.catalog
    cat.items = []
    cat.by_symbol = {}
    cat.loaded_at = time.time()
    cat.error = ""
    items = await cat.search("588200", 14)
    check(any(i["symbol"] == "tx:sh588200" for i in items),
          "Catalog.search('588200') 未返回 tx:sh588200")
    record("Catalog.search 全链路", f"{len(items)} 条")


# ---------------- 3. K 线 / 元信息 / 周期限制 ----------------

async def test_klines() -> None:
    print("\n[K线] 六标的 + 裸码兼容")
    cases = [
        ("tx:sh588200", "1d", "科创芯片ETF"),
        ("tx:sh600519", "1d", "贵州茅台"),
        ("tx:sz000001", "1d", "平安银行"),
        ("tx:sh510300", "1d", "沪深300ETF"),
        ("tx:sh000001", "1d", "上证指数"),
        ("tx:sh000300", "1d", "沪深300"),
        ("tx:hk00700", "1d", "腾讯控股"),
        ("tx:usAAPL.OQ", "1d", "苹果"),
        ("tx:588200", "1d", "裸码兼容"),     # 历史收藏里的旧 symbol
        ("tx:sh588200", "1w", "周线"),
    ]
    for sym, per, label in cases:
        d = await symbols.fetch_klines(sym, per, 800)
        check(d["count"] > 0, f"{sym} {per} 无 K 线")
        check(d["bars"][-1][0] > d["bars"][0][0], f"{sym} {per} 时间未升序")
        check(d["name"], f"{sym} 名称为空")
        check(d["periods"] == ["1d", "1w"], f"{sym} 周期能力不对：{d['periods']}")
        record(f"K线 {sym} {per}",
               f"{d['count']} 根 / {d['name']} / 最新 {d['bars'][-1][4]}")

    try:
        await symbols.fetch_klines("tx:sh588200", "15m", 800)
    except ProviderError as exc:
        check(exc.code == "PERIOD_UNSUPPORTED", "15m 应报周期不支持")
        record("周期限制提示", exc.message)
    else:
        raise AssertionError("tx 源竟然返回了 15m 数据")

    # 本地库二次读取（零网络命中）
    t0 = time.time()
    d = await symbols.fetch_klines("tx:sh588200", "1d", 800)
    check(d["count"] > 0, "本地库二次读取失败")
    record("本地库命中", f"{round((time.time() - t0) * 1000)}ms / {d['servedFrom']}")


# ---------------- 4. 报价（价位条 / 托盘） ----------------

async def test_quotes() -> None:
    print("\n[报价] 批量 + 裸码")
    wanted = ["tx:sh588200", "tx:sh600519", "tx:sz000001",
              "tx:hk00700", "tx:usAAPL.OQ", "tx:588200"]
    items, errors = await symbols.fetch_quotes(wanted)
    by = {i["symbol"]: i for i in items}
    for s in wanted:
        check(s in by, f"{s} 无报价：{errors}")
        check(by[s]["last"] > 0, f"{s} 价格非法：{by[s]['last']}")
    record("批量报价", f"{len(items)}/{len(wanted)} 成功 / " +
           " ".join(f"{s.split(':')[1]}={by[s]['last']}" for s in wanted[:3]))
    if errors:
        print("  (提示)", errors)


# ---------------- 5. 历史分页（左拖加载更早） ----------------

async def test_history() -> None:
    print("\n[历史] 向左翻页")
    d = await symbols.fetch_klines("tx:sh588200", "1d", 800)
    first = int(d["bars"][0][0])
    h = await symbols.fetch_history("tx:sh588200", "1d", first, 400)
    check(h["count"] > 0, "历史分页没拿到更早的 K 线")
    check(all(b[0] < first for b in h["bars"]), "历史分页返回了不早于锚点的柱子")
    record("历史分页", f"新增 {h['count']} 根 / hasMore={h['hasMore']}")

    # 翻到上市之前：应正常返回空，而不是抛 ProviderError
    very_old = 315532800          # 1980-01-01 UTC
    bars = await tencent.klines_before("sh588200", "1d", very_old, 400)
    check(bars == [], "翻到上市之前应返回空列表")
    record("翻到尽头不报错", "返回 []")


# ---------------- 6. HTTP API 冒烟（贴近前端实际调用） ----------------

async def test_http() -> None:
    import aiohttp
    from aiohttp import web

    from backend import api

    print("\n[HTTP] /api/search + /api/kline")
    app = web.Application()
    app.router.add_get("/api/search", api.search_handler)
    app.router.add_get("/api/kline", api.kline)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(base + "/api/search", params={"q": "588200"}) as r:
                js = await r.json()
            hit = next((i for i in js.get("items", []) if i["symbol"] == "tx:sh588200"), None)
            check(hit is not None, "/api/search 未返回 tx:sh588200")
            record("GET /api/search?q=588200", f"{hit['name']} / {hit['marketLabel']}")

            async with s.get(base + "/api/kline", params={
                "symbol": "tx:sh588200", "period": "1d", "limit": "800",
            }) as r:
                check(r.status == 200, f"/api/kline 1d 返回 {r.status}：{await r.text()}")
                d = await r.json()
            check(d.get("count", 0) > 0, "/api/kline 1d 无 K 线")
            check(d.get("name") == hit["name"], "K 线返回的中文名与搜索结果不一致")
            record("GET /api/kline 1d", f"{d['count']} 根 / {d['name']} / {d['sourceLabel']}")

            # 前端默认可能是 15m，服务端应给出可识别的周期不支持错误，由前端自动切 1D
            async with s.get(base + "/api/kline", params={
                "symbol": "tx:sh588200", "period": "15m",
            }) as r:
                check(r.status == 400, f"15m 应返回 400，实际 {r.status}")
                err = (await r.json())["error"]
            check(err["code"] == "PERIOD_UNSUPPORTED", f"错误码不对：{err}")
            record("GET /api/kline 15m", f"400 {err['code']}（前端自动切 1D）")
    finally:
        await runner.cleanup()


async def main() -> None:
    print("腾讯真实盘数据源回归测试（真实网络）")
    with tempfile.TemporaryDirectory(prefix="moyu-cnsrc-") as td:
        config.BARS_DIR = Path(td)
        kstore._mem.clear()
        test_normalize()
        try:
            await test_search()
            await test_klines()
            await test_quotes()
            await test_history()
            await test_http()
        finally:
            await net.close()
    print(f"\n全部通过：{len(OK)} 项检查")
    if FAIL:
        print("失败：", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        sys.exit(1)
