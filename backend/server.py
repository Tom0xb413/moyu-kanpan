"""aiohttp 应用：同源托管前端 + 数据 API + 窗口控制。"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from . import api, cache, config, kstore, prefs
from . import search as search_mod
from . import symbols as symbols_mod
from . import ticker as ticker_mod
from . import win32

log = logging.getLogger("server")
NO_CACHE = {"Cache-Control": "no-store, max-age=0"}


def _hwnd(ctx: dict[str, Any]) -> int:
    h = ctx.get("hwnd")
    if callable(h):
        try:
            return int(h() or 0)
        except Exception:
            return 0
    return int(h or 0)


@web.middleware
async def auth_mw(request: web.Request, handler):
    if request.path.startswith("/api"):
        token = request.app["token"]
        given = request.query.get("k") or request.headers.get("X-App-Key") or ""
        if token and given != token:
            return web.json_response({"error": {"code": "FORBIDDEN", "message": "token 无效"}}, status=403)
    return await handler(request)


@web.middleware
async def error_mw(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        log.exception("请求失败 %s", request.path)
        return web.json_response({"error": {"code": "INTERNAL", "message": str(exc)}}, status=500)


@web.middleware
async def cache_mw(request: web.Request, handler):
    """前端资源不缓存，避免升级后仍跑旧样式/脚本。"""
    resp = await handler(request)
    if request.path.startswith(("/css/", "/js/")) or request.path in ("/", "/index.html"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


async def index(request: web.Request) -> web.Response:
    path = config.FRONTEND_DIR / "index.html"
    if not path.exists():
        return web.Response(text="frontend/index.html 缺失", status=500)
    token = request.app["token"]
    html = path.read_text(encoding="utf-8").replace("__TOKEN__", token)
    return web.Response(text=html, content_type="text/html", charset="utf-8", headers=NO_CACHE)


async def favicon(request: web.Request) -> web.Response:
    ico = config.BASE_DIR / "assets" / "app.ico"
    if ico.exists():
        return web.FileResponse(ico)
    return web.Response(status=204)


# ---------- 窗口控制 ----------
async def win_drag(request: web.Request) -> web.Response:
    ctx = request.app["ctx"]
    hwnd = _hwnd(ctx)
    log.debug("拖动请求 hwnd=%s", hwnd)
    win32.drag_async(hwnd)
    return web.json_response({"ok": True})


async def win_resize(request: web.Request) -> web.Response:
    ctx = request.app["ctx"]
    edge = request.query.get("edge", "br")
    win32.resize_async(_hwnd(ctx), edge)
    return web.json_response({"ok": True})


async def win_topmost(request: web.Request) -> web.Response:
    ctx = request.app["ctx"]
    on = request.query.get("on", "1") in ("1", "true", "yes")
    win32.set_topmost(_hwnd(ctx), on)
    win = ctx.get("win")
    if win is not None:
        try:
            win.on_top = on
        except Exception:
            pass
    prefs.save({"on_top": on})
    return web.json_response({"ok": True, "on": on})


async def win_opacity(request: web.Request) -> web.Response:
    ctx = request.app["ctx"]
    try:
        v = float(request.query.get("v", "0.95"))
    except ValueError:
        v = 0.95
    v = max(0.35, min(1.0, v))
    if ctx.get("mode") == "strip":
        prefs.save({"strip_alpha": v})
    else:
        prefs.save({"alpha": v})
    if ctx.get("native_alpha"):
        win32.set_alpha(_hwnd(ctx), v)
    return web.json_response({"ok": True, "v": v})


async def win_hide(request: web.Request) -> web.Response:
    ctx = request.app["ctx"]
    fn = ctx.get("hide")
    if callable(fn):
        fn()
    return web.json_response({"ok": True})


async def win_show(request: web.Request) -> web.Response:
    ctx = request.app["ctx"]
    fn = ctx.get("show")
    if callable(fn):
        fn()
    return web.json_response({"ok": True})


async def win_state(request: web.Request) -> web.Response:
    ctx = request.app["ctx"]
    hwnd = _hwnd(ctx)
    rect = win32.get_rect(hwnd) if hwnd else (0, 0, 0, 0)
    p = prefs.load()
    return web.json_response(
        {
            "hwnd": hwnd,
            "visible": bool(ctx.get("visible", True)),
            "mode": ctx.get("mode", "full"),
            "hotkey": ctx.get("hotkey_label", ""),
            "hotkeyOk": bool(ctx.get("hotkey_ok", False)),
            "rect": list(rect),
            "nativeAlpha": bool(ctx.get("native_alpha", False)),
            "tray": bool(ctx.get("tray_ok", False)),
            # 关闭行为：pending 由 on_closing 置位，前端轮询到就弹问询卡
            "exitPending": bool(ctx.get("exit_pending", False)),
            "exitAsk": bool(p.get("exit_ask", True)),
            "exitAction": str(p.get("exit_action") or "tray"),
            "trayMinimized": bool(ctx.get("tray_minimized", False)),
            "stripDir": str(p.get("strip_dir") or "h"),
        }
    )


async def win_mode(request: web.Request) -> web.Response:
    ctx = request.app["ctx"]
    mode = request.query.get("mode", "full")
    if mode not in ("full", "strip"):
        return web.json_response({"error": {"code": "BAD_MODE", "message": mode}}, status=400)
    fn = ctx.get("set_mode")
    ok = False
    if callable(fn):
        try:
            ok = bool(fn(mode))
        except Exception as exc:
            log.warning("切换窗口模式失败: %s", exc)
    return web.json_response({"ok": ok, "mode": mode})


async def win_quit(request: web.Request) -> web.Response:
    ctx = request.app["ctx"]
    fn = ctx.get("quit")
    if callable(fn):
        fn()
    return web.json_response({"ok": True})


async def win_close(request: web.Request) -> web.Response:
    """问询结果：quit=真退出 / tray=只留价位条与托盘 / cancel=取消。"""
    ctx = request.app["ctx"]
    action = request.query.get("action", "cancel")
    if action not in ("quit", "tray", "cancel"):
        action = "cancel"
    remember = request.query.get("remember") in ("1", "true", "yes")
    fn = ctx.get("close")
    ok = False
    if callable(fn):
        try:
            ok = bool(fn(action, remember))
        except Exception as exc:
            log.warning("处理关闭选择失败: %s", exc)
    return web.json_response({"ok": ok, "action": action})


async def win_strip_dir(request: web.Request) -> web.Response:
    """价位条方向：h=横向 5 列 / v=竖向 5 行。"""
    ctx = request.app["ctx"]
    d = "v" if request.query.get("dir") == "v" else "h"
    fn = ctx.get("set_strip_dir")
    if callable(fn):
        try:
            d = str(fn(d))
        except Exception as exc:
            log.warning("切换价位条方向失败: %s", exc)
    return web.json_response({"ok": True, "dir": d})


def build_app(ctx: dict[str, Any], token: str) -> web.Application:
    app = web.Application(middlewares=[error_mw, cache_mw, auth_mw], client_max_size=1 << 20)
    app["ctx"] = ctx
    app["token"] = token

    async def _warm(app_: web.Application) -> None:
        async def _go() -> None:
            try:
                await search_mod.gatef.warm()
                await search_mod.catalog.ensure()
                log.info("标的索引预热完成：%d 条", len(search_mod.catalog.items))
            except Exception as exc:
                log.warning("索引预热失败: %s", exc)

            # 旧版 K 线快照已由 kstore 取代，清掉孤儿文件
            try:
                cache.prune_snapshots(200)
            except Exception:
                pass

            await asyncio.sleep(config.WARM_DELAY)
            try:
                res = await symbols_mod.warm_watch()
                log.info("收藏预热：%s", res)
            except Exception as exc:
                log.warning("收藏预热失败: %s", exc)

            try:
                watch = [
                    w["symbol"]
                    for w in prefs.normalize_list(prefs.load().get("watch"), prefs.WATCH_MAX)
                ]
                strip = [
                    w["symbol"]
                    for w in prefs.normalize_list(prefs.load().get("strip"), prefs.STRIP_MAX)
                ]
                removed = kstore.prune(watch + strip)
                log.info("本地 K 线库清理：%d 个文件", removed)
            except Exception as exc:
                log.debug("本地 K 线库清理失败: %s", exc)

        asyncio.create_task(_go())
        # 托盘 / 滚动条用的后台报价刷新
        ticker_mod.start()

    app.on_startup.append(_warm)

    app.router.add_get("/", index)
    app.router.add_get("/index.html", index)
    app.router.add_get("/favicon.ico", favicon)

    if config.FRONTEND_DIR.exists():
        app.router.add_static("/css/", config.FRONTEND_DIR / "css")
        app.router.add_static("/js/", config.FRONTEND_DIR / "js")
    if config.VENDOR_DIR.exists():
        app.router.add_static("/vendor/", config.VENDOR_DIR)

    app.router.add_get("/api/health", api.health)
    app.router.add_get("/api/search", api.search_handler)
    app.router.add_get("/api/kline", api.kline)
    app.router.add_get("/api/quote", api.quote)
    app.router.add_get("/api/ticker", api.ticker)
    app.router.add_get("/api/capabilities", api.capability)
    app.router.add_get("/api/symbols", api.symbol_list)
    app.router.add_get("/api/prefs", api.prefs_get)
    app.router.add_post("/api/prefs", api.prefs_post)
    app.router.add_post("/api/warm", api.warm)
    app.router.add_get("/api/diag", api.diag)

    app.router.add_post("/api/win/drag", win_drag)
    app.router.add_post("/api/win/resize", win_resize)
    app.router.add_post("/api/win/topmost", win_topmost)
    app.router.add_post("/api/win/opacity", win_opacity)
    app.router.add_post("/api/win/hide", win_hide)
    app.router.add_post("/api/win/show", win_show)
    app.router.add_post("/api/win/mode", win_mode)
    app.router.add_post("/api/win/quit", win_quit)
    app.router.add_post("/api/win/close", win_close)
    app.router.add_post("/api/win/strip-dir", win_strip_dir)
    app.router.add_get("/api/win/state", win_state)
    return app
