"""摸鱼看盘 · 入口。无边框置顶小窗 + 本地 aiohttp 服务。"""
from __future__ import annotations

import os
import sys

# 必须在 import webview 之前固定 .NET 运行时，并清掉代理变量（否则上游超时）
os.environ.setdefault("PYTHONNET_RUNTIME", "netfx")
for _k in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
):
    os.environ.pop(_k, None)

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import asyncio
import ctypes
import logging
import secrets
import threading
import time

from backend import config, prefs, server, win32
from backend.hotkey import HotkeyThread
from backend import ticker as ticker_mod
from backend.tray import Tray

log = logging.getLogger("app")
MUTEX_NAME = "Global\\MoYuKanPan.SingleInstance.v1"


def _acquire_single_instance() -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            hwnd = win32.find_hwnd(config.APP_TITLE)
            if hwnd:
                win32.bring_front(hwnd)
            return False
        globals()["_MUTEX"] = handle
        return True
    except Exception:
        return True


def _strip_size(p: dict) -> tuple[int, int]:
    """收起条尺寸：横向 5 列 / 竖向 5 行。"""
    if (p or {}).get("strip_dir") == "v":
        return int(config.STRIP_V_W), int(config.STRIP_V_H)
    return int((p or {}).get("strip_w") or config.STRIP_W), int(config.STRIP_H)


def _start_server(token: str, ctx: dict) -> int:
    from aiohttp import web

    ready = threading.Event()
    box: dict[str, int] = {}

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _boot() -> None:
            app = server.build_app(ctx, token)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            box["port"] = int(port)
            ready.set()
            log.info("本地服务已启动 127.0.0.1:%s", port)
            await asyncio.Event().wait()

        try:
            loop.run_until_complete(_boot())
        except Exception:
            log.exception("本地服务异常退出")
            ready.set()

    threading.Thread(target=_run, daemon=True, name="http").start()
    ready.wait(10)
    return box.get("port", 0)


def main() -> None:
    import webview

    config.setups_logging(debug=os.getenv("MOKU_DEBUG") == "1")
    log.info("=== %s v%s 启动 ===", config.APP_NAME, config.VERSION)

    if not _acquire_single_instance():
        log.info("已有实例在运行，唤回后退出")
        time.sleep(0.4)
        return

    p = prefs.load()
    token = secrets.token_urlsafe(18)
    ctx: dict = {
        "win": None,
        "hwnd": lambda: ctx.get("_hwnd", 0),
        "visible": True,
        "mode": "full",
        "hide": None,
        "show": None,
        "quit": None,
        "set_mode": None,
        "hotkey_label": "",
        "hotkey_ok": False,
        "native_alpha": False,
        "tray_ok": False,
        # 关闭行为：pending=等前端问询，resume=问询前的窗口模式
        "exit_pending": False,
        "exit_resume_mode": None,
        "tray_minimized": False,
        "force_quit": False,
        "strip_dir": "h" if p.get("strip_dir") != "v" else "v",
        "close": None,
        "set_strip_dir": None,
    }

    port = _start_server(token, ctx)
    if not port:
        log.error("本地服务启动失败")
        return

    start_mode = "strip" if p.get("mode") == "strip" else "full"
    if start_mode == "strip":
        width, height = _strip_size(p)
        x, y = None, None
    else:
        width = int(p.get("w") or 520)
        height = int(p.get("h") or 400)
        x, y = p.get("x"), p.get("y")
    # 透明窗 + WebView2 在部分机器上会丢背景，默认「不透明窗 + 整窗 alpha」，更稳
    transparent = os.getenv("MOKU_TRANSPARENT") == "1"
    ctx["native_alpha"] = not transparent

    win = webview.create_window(
        config.APP_TITLE,
        url=f"http://127.0.0.1:{port}/?k={token}",
        width=width,
        height=height,
        x=x,
        y=y,
        # 最小尺寸放到竖向价位条的宽度；完整窗的 340×280 由缩放热区自己约束
        min_size=(config.STRIP_MIN_W, config.STRIP_MIN_H),
        frameless=True,
        easy_drag=False,  # 关键：让图表自己处理拖拽平移
        resizable=True,
        on_top=bool(p.get("on_top", True)),
        transparent=transparent,
        background_color="#FFFFFF",
        shadow=False,
        confirm_close=False,
        text_select=False,
        zoomable=False,
    )
    ctx["win"] = win

    def show_win() -> None:
        # 从「只留托盘 + 价位条」唤回时，直接还原完整窗口
        if ctx.get("tray_minimized"):
            restore_from_tray()
            return
        try:
            win.show()
            hwnd = ctx.get("_hwnd") or 0
            # SW_RESTORE 会把收起条弹回「还原位置」；收起状态本身置顶，无需再置前
            if hwnd and ctx.get("mode") != "strip":
                win32.bring_front(hwnd)
            ctx["visible"] = True
            ticker_mod.visible = True
        except Exception as exc:
            log.warning("show 失败: %s", exc)

    def hide_win() -> None:
        try:
            win.hide()
            ctx["visible"] = False
            ticker_mod.visible = False
        except Exception as exc:
            log.warning("hide 失败: %s", exc)

    def toggle_win() -> None:
        if ctx.get("tray_minimized"):
            restore_from_tray()
            return
        if ctx.get("visible", True):
            hide_win()
        else:
            show_win()

    def toggle_alpha(mode: str) -> None:
        hwnd = ctx.get("_hwnd") or 0
        if not hwnd or not ctx.get("native_alpha"):
            return
        cur = prefs.load()
        if mode == "strip":
            win32.set_alpha(hwnd, float(cur.get("strip_alpha") or config.STRIP_ALPHA))
        else:
            win32.set_alpha(hwnd, float(cur.get("alpha") or 1.0))

    def apply_mode(mode: str, save: bool = True) -> bool:
        """全窗 <-> 滚动条：改尺寸、位置、透明度，并记住状态。"""
        mode = "strip" if mode == "strip" else "full"
        hwnd = ctx.get("_hwnd") or 0
        ctx["mode"] = mode
        # 只要回到完整窗口，就撤销「只剩托盘 + 价位条」的任务栏隐藏
        if mode == "full" and ctx.get("tray_minimized"):
            ctx["tray_minimized"] = False
            if hwnd:
                win32.set_toolwindow(hwnd, False)
        if not hwnd:
            if save:
                prefs.save({"mode": mode})
            return False
        cur = prefs.load()
        dw, dh = win32.frame_delta(hwnd)
        try:
            if mode == "strip":
                # 先把主窗几何记下来，收起后再关就不会把 30px 当成主窗大小
                _save_geometry(win)
                sw, sh = _strip_size(cur)
                left, top, right, bottom = win32.work_area(hwnd)
                x = right - (sw + dw) - 14
                y = bottom - (sh + dh) - 10
                win32.set_window_rect(hwnd, x, y, sw + dw, sh + dh)
                win32.clamp_to_work_area(hwnd, left, top, right, bottom, 10)
                toggle_alpha("strip")
                win32.set_topmost(hwnd, True)
            else:
                w = int(cur.get("w") or 520)
                h = int(cur.get("h") or 400)
                gx, gy = cur.get("x"), cur.get("y")
                left, top, right, bottom = win32.work_area(hwnd)
                if gx is None or gy is None:
                    gx = right - (w + dw) - 40
                    gy = bottom - (h + dh) - 60
                win32.set_window_rect(hwnd, int(gx), int(gy), w + dw, h + dh)
                win32.clamp_to_work_area(hwnd, left, top, right, bottom, 8)
                toggle_alpha("full")
                win32.set_topmost(hwnd, bool(cur.get("on_top", True)))
        except Exception as exc:
            log.warning("切换模式失败: %s", exc)
            return False
        if save:
            prefs.save({"mode": mode})
        # 页面会通过 /api/win/state 轮询感知托盘/菜单触发的模式切换，
        # 这里不直接 evaluate_js：HTTP 线程跨线程操作 WebView2 容易卡死。
        log.info("窗口模式 -> %s", mode)
        return True

    def quit_app() -> None:
        # win.destroy() 同样会触发 on_closing，这里必须放行，否则永远退不出去
        ctx["force_quit"] = True
        try:
            tray.stop()
        except Exception:
            pass
        try:
            if ctx.get("mode") == "full":
                _save_geometry(win)
            win.destroy()
        except Exception:
            os._exit(0)

    def tray_minimize(save: bool = True) -> None:
        """隐藏完整窗口、从任务栏移除，只留实时价位条 + 托盘图标。"""
        hwnd = ctx.get("_hwnd") or 0
        if ctx.get("mode") == "full":
            _save_geometry(win)
        apply_mode("strip", save=save)
        if hwnd:
            win32.set_toolwindow(hwnd, True)
        ctx["tray_minimized"] = True
        ctx["visible"] = True
        ticker_mod.visible = True
        log.info("已最小化到托盘（保留实时价位条）")

    def restore_from_tray() -> None:
        hwnd = ctx.get("_hwnd") or 0
        ctx["tray_minimized"] = False
        if hwnd:
            win32.set_toolwindow(hwnd, False)
        apply_mode("full")
        if hwnd:
            win32.bring_front(hwnd)
        ctx["visible"] = True
        ticker_mod.visible = True
        log.info("从托盘还原完整窗口")

    def close_answer(action: str, remember: bool = False) -> bool:
        """前端问询结果；cancel 时回到问询前的窗口模式。"""
        ctx["exit_pending"] = False
        resume = ctx.pop("exit_resume_mode", None)
        if action in ("quit", "tray") and remember:
            prefs.save({"exit_ask": False, "exit_action": action})
        if action == "quit":
            quit_app()
        elif action == "tray":
            tray_minimize()
        else:
            if resume == "strip" and ctx.get("mode") == "full":
                apply_mode("strip", save=False)
            log.info("取消关闭")
        return True

    def set_strip_dir(d: str) -> str:
        d = "v" if d == "v" else "h"
        prefs.save({"strip_dir": d})
        ctx["strip_dir"] = d
        if ctx.get("mode") == "strip":
            apply_mode("strip", save=False)
        return d

    ctx.update({
        "show": show_win, "hide": hide_win, "quit": quit_app,
        "set_mode": apply_mode, "close": close_answer,
        "set_strip_dir": set_strip_dir,
    })

    hk = HotkeyThread(toggle_win)
    hk.start()

    tray = Tray(
        on_toggle=lambda: toggle_win(),
        on_strip=lambda: (show_win(), apply_mode("strip")),
        on_quit=quit_app,
        title=config.APP_TITLE,
        icon_path=str(config.BASE_DIR / "assets" / "app.ico"),
    )
    ticker_mod.set_listener(lambda info: tray.set_tip(_tray_tip(info)))

    def after_start(app_window=None) -> None:
        hwnd = 0
        for _ in range(30):
            hwnd = win32.find_hwnd(config.APP_TITLE)
            if hwnd:
                break
            time.sleep(0.2)
        ctx["_hwnd"] = hwnd
        # 允许窗口缩到滚动条高度（WinForms 默认最小尺寸会拦住）
        win32.set_min_size(win, config.STRIP_MIN_W, config.STRIP_MIN_H)
        if hwnd:
            win32.round_corners(hwnd)
            win32.set_topmost(hwnd, bool(p.get("on_top", True)))
            # 不在启动时抢焦点：窗口本来就是可见的，隐身工具不该打断办公
            if start_mode == "strip":
                apply_mode("strip", save=False)
            elif ctx.get("native_alpha"):
                win32.set_alpha(hwnd, float(p.get("alpha") or 1.0))
            log.info("窗口句柄 %s，客户区 %sx%s", hwnd, *win32.get_client_size(hwnd))
        else:
            log.warning("未找到窗口句柄，原生拖动不可用")

        ctx["tray_ok"] = tray.start()

        for _ in range(12):
            if hk.ok or hk.registered:
                break
            time.sleep(0.1)
        ctx["hotkey_ok"] = hk.ok
        ctx["hotkey_label"] = hk.label
        prefs.save({"hotkey": {"label": hk.label or "不可用"}})

    def on_closing() -> bool:
        """点 X / Alt+F4：不再直接退出，改为问询或按已保存的默认动作。

        返回 False 会令 pywebview 取消 FormClosing（closing 事件是同步的）。
        """
        if ctx.get("force_quit"):
            return True
        if ctx.get("mode") == "full":
            _save_geometry(win)
        p = prefs.load()
        if not p.get("exit_ask", True):
            if p.get("exit_action") == "quit":
                return True
            tray_minimize()
            return False
        # 需要问询：价位条太小放不下卡片，先临时展开，取消时再收回去
        ctx["exit_resume_mode"] = ctx.get("mode")
        if ctx.get("mode") == "strip":
            apply_mode("full", save=False)
        ctx["exit_pending"] = True
        log.info("关闭请求转为前端问询")
        return False

    try:
        win.events.closing += on_closing
    except Exception:
        pass

    log.info("进入窗口主循环（透明=%s，模式=%s，热键=%s）", transparent, start_mode, hk.label or "待定")
    webview.start(
        func=after_start,
        gui="edgechromium",
        debug=os.getenv("MOKU_DEBUG") == "1",
        private_mode=True,
        storage_path=str(config.DATA_DIR / "webview"),
    )
    try:
        tray.stop()
    except Exception:
        pass
    log.info("窗口已关闭，退出")


def _fmt_price(v: float) -> str:
    a = abs(v or 0)
    if a >= 1000:
        return f"{v:,.2f}"
    if a >= 1:
        return f"{v:.4f}".rstrip("0").rstrip(".")
    if a >= 0.01:
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return f"{v:.8f}".rstrip("0").rstrip(".")


def _tray_tip(info: dict) -> str:
    """托盘悬停提示：第一个滚动条标的的最新价。"""
    first = (info or {}).get("first") or {}
    name = first.get("name") or ""
    last = first.get("last")
    if not name or last is None:
        return config.APP_TITLE
    try:
        chg = float(first.get("chgPct"))
    except (TypeError, ValueError):
        chg = None
    pct = ""
    if chg is not None:
        pct = (" ▲" if chg >= 0 else " ▼") + f"{abs(chg):.2f}%"
    return f"{name} {_fmt_price(float(last))}{pct}"


def _save_geometry(win) -> None:
    try:
        hwnd = win32.hwnd_of(win)
        if hwnd:
            x0, y0, x1, y1 = win32.get_rect(hwnd)
            dw, dh = win32.frame_delta(hwnd)
            prefs.save(
                {
                    "w": max(340, (x1 - x0) - dw),
                    "h": max(280, (y1 - y0) - dh),
                    "x": int(x0),
                    "y": int(y0),
                }
            )
            return
        prefs.save(
            {
                "w": int(win.width),
                "h": int(win.height),
                "x": int(win.x),
                "y": int(win.y),
            }
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
