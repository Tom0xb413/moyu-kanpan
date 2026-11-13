"""临时探针：无头 Edge 打开真实前端，检查切换周期后的可视区间。

不弹窗、不写用户 prefs/缓存（全部指向临时目录）。
用法：python tests/probe_zoom.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp import web
from playwright.sync_api import sync_playwright

from backend import config, prefs, server

TOKEN = "probe-token"
OUT = Path(__file__).resolve().parent.parent / ".workbuddy" / "shots"
OUT.mkdir(parents=True, exist_ok=True)


def start_server(port_box: dict) -> None:
    async def boot() -> None:
        app = server.build_app(
            {
                "win": None,
                "hwnd": lambda: 0,
                "visible": True,
                "mode": "full",
                "hide": lambda: None,
                "show": lambda: None,
                "quit": lambda: None,
                "set_mode": lambda m: m,
                "hotkey_label": "",
                "hotkey_ok": False,
                "native_alpha": False,
                "tray_ok": False,
                "exit_pending": False,
                "exit_resume_mode": None,
                "tray_minimized": False,
                "force_quit": False,
                "strip_dir": "h",
                "close": lambda *a, **k: None,
                "set_strip_dir": lambda d: d,
            },
            TOKEN,
        )
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port_box["port"] = site._server.sockets[0].getsockname()[1]
        port_box["ready"].set()
        await asyncio.Event().wait()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(boot())


def read(page, label: str) -> dict:
    info = page.evaluate(
        """() => {
            const c = document.getElementById('chart');
            const r = ChartView.logicalRange();
            const w = c ? c.clientWidth : 0;
            return {
                n: ChartView.count(),
                range: r ? {from: +r.from.toFixed(2), to: +r.to.toFixed(2)} : null,
                width: w,
                spacing: r && w ? +(w / (r.to - r.from)).toFixed(2) : null,
                period: (document.querySelector('#tf button.active') || {}).dataset?.p || '',
                status: (document.getElementById('stLeft') || {}).textContent || '',
            };
        }"""
    )
    print(f"  {label}: {json.dumps(info, ensure_ascii=False)}")
    return info


def main() -> None:
    print("启动隔离测试服务（临时 prefs/bars）…")
    port_box = {"ready": threading.Event()}
    threading.Thread(target=start_server, args=(port_box,), daemon=True).start()
    if not port_box["ready"].wait(15):
        raise SystemExit("测试服务启动失败")
    port = port_box["port"]
    print("服务端口", port)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        for vw, vh in ((632, 453), (545, 376), (340, 280)):
            page = browser.new_page(viewport={"width": vw, "height": vh}, device_scale_factor=1)
            page.goto(f"http://127.0.0.1:{port}/?k={TOKEN}&s=gf:BTC_USDT&p=15m", wait_until="load")
            page.wait_for_function("window.ChartView && ChartView.count() > 0", timeout=45000)
            time.sleep(0.8)

            print(f"\n===== 视口 {vw}x{vh} =====")
            read(page, "15m")
            page.locator("#chart").screenshot(path=str(OUT / f"zoom-{vw}-15m.png"))

            # 模拟用户向右拖出空白（负逻辑区间）后再切周期
            page.evaluate("ChartView.setLogicalRange({from: -120, to: 44})")
            time.sleep(0.4)
            print("  已把视口拖到负区间")
            read(page, "negative")
            page.click("#tf button[data-p='1h']")
            time.sleep(1.6)
            read(page, "1h-after-negative")
            page.locator("#chart").screenshot(path=str(OUT / f"zoom-{vw}-1h.png"))

            # 再手动放大到 60 根，切 4H
            page.evaluate("ChartView.setLogicalRange({from: 600, to: 660})")
            time.sleep(0.3)
            page.click("#tf button[data-p='4h']")
            time.sleep(1.6)
            read(page, "4h-after-zoom")
            page.locator("#chart").screenshot(path=str(OUT / f"zoom-{vw}-4h.png"))

            if vw == 545:
                # 历史分页：先等保护期过去，再拖到最左触发加载更早
                time.sleep(1.8)
                page.evaluate("ChartView.setLogicalRange({from: 0, to: 120})")
                time.sleep(2.6)
                read(page, "after-load-earlier")
                page.locator("#chart").screenshot(path=str(OUT / "zoom-load-earlier.png"))

                # 大周期切换
                page.click("#tf button[data-p='1d']")
                time.sleep(1.6)
                read(page, "1d")
                page.locator("#chart").screenshot(path=str(OUT / "zoom-1d.png"))
                page.click("#tf button[data-p='1w']")
                time.sleep(1.6)
                read(page, "1w")
                page.locator("#chart").screenshot(path=str(OUT / "zoom-1w.png"))
            page.close()

        browser.close()


if __name__ == "__main__":
    import os
    import shutil

    with tempfile.TemporaryDirectory(prefix="moyu-probe-") as td:
        config.DATA_DIR = Path(td)
        config.BARS_DIR = Path(td) / "bars"
        config.CACHE_DIR = Path(td) / "cache"
        config.PREFS_FILE = Path(td) / "prefs.json"
        config.BARS_DIR.mkdir(parents=True, exist_ok=True)
        # 用真实缓存的副本复现（只读源目录，不影响用户数据）
        real = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MoYuKanPan" / "bars"
        if real.exists():
            shutil.copytree(real, config.BARS_DIR, dirs_exist_ok=True)
            print("已复制真实缓存", len(list(config.BARS_DIR.glob('*.json'))), "个文件")
        prefs._state = None
        main()
