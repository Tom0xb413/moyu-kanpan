"""Win32 窗口细节：无边框拖动/缩放、置顶、圆角、透明度兜底。"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes as wt

log = logging.getLogger("win32")

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2
HT = {
    "tl": 13,
    "t": 12,
    "tr": 14,
    "l": 10,
    "r": 11,
    "bl": 16,
    "b": 15,
    "br": 17,
}
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
LWA_ALPHA = 0x2
DWMWA_WINDOW_CORNER_PREFERENCE = 33


def hwnd_of(win) -> int:
    if win is None:
        return 0
    for attr in ("native", "_native"):
        native = getattr(win, attr, None)
        if native is None:
            continue
        try:
            return int(native.Handle.ToInt64())
        except Exception:
            try:
                return int(native.Handle)
            except Exception:
                continue
    return 0


def get_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def get_client_size(hwnd: int) -> tuple[int, int]:
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
    ]


def work_area(hwnd: int = 0) -> tuple[int, int, int, int]:
    """返回窗口所在显示器的可用区域（已扣掉任务栏）。"""
    try:
        hmon = user32.MonitorFromWindow(hwnd or 0, 2)  # MONITOR_DEFAULTTONEAREST
        if hmon:
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                r = mi.rcWork
                return int(r.left), int(r.top), int(r.right), int(r.bottom)
    except Exception as exc:
        log.debug("work_area 失败: %s", exc)
    return 0, 0, int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def set_window_rect(hwnd: int, x: int, y: int, w: int, h: int) -> bool:
    if not hwnd:
        return False
    try:
        return bool(
            user32.SetWindowPos(
                hwnd, 0, int(x), int(y), int(w), int(h),
                SWP_NOZORDER | SWP_NOACTIVATE,
            )
        )
    except Exception as exc:
        log.warning("set_window_rect 失败: %s", exc)
        return False


def clamp_to_work_area(hwnd: int, left: int, top: int, right: int, bottom: int,
                       margin: int = 8) -> tuple[int, int]:
    """回读实际 rect 并钳制进工作区；DWM 隐形边框会让 SetWindowPos 产生几像素偏移。"""
    if not hwnd:
        return 0, 0
    for _ in range(3):
        x0, y0, x1, y1 = get_rect(hwnd)
        w, h = x1 - x0, y1 - y0
        nx = min(x0, right - w - margin)
        ny = min(y0, bottom - h - margin)
        nx = max(left + margin, nx)
        ny = max(top + margin, ny)
        if (nx, ny) == (x0, y0):
            break
        if not set_window_rect(hwnd, nx, ny, w, h):
            break
    return get_rect(hwnd)[:2]


def frame_delta(hwnd: int) -> tuple[int, int]:
    """窗口边框带来的差值：(外框 - 客户区)。"""
    if not hwnd:
        return 0, 0
    try:
        r = get_rect(hwnd)
        cw, ch = get_client_size(hwnd)
        return max(0, (r[2] - r[0]) - cw), max(0, (r[3] - r[1]) - ch)
    except Exception:
        return 0, 0


def set_min_size(win, w: int, h: int) -> bool:
    """动态改 WinForms 的最小尺寸（收起成 30px 高时必须放开）。"""
    try:
        from System.Drawing import Size  # type: ignore

        native = getattr(win, "native", None)
        if native is None:
            return False
        native.MinimumSize = Size(int(w), int(h))
        return True
    except Exception as exc:
        log.debug("set_min_size 失败: %s", exc)
        return False


def native_drag(hwnd: int) -> None:
    if not hwnd:
        return
    try:
        user32.ReleaseCapture()
        user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)
    except Exception as exc:
        log.warning("native_drag 失败: %s", exc)


def native_resize(hwnd: int, edge: str) -> None:
    if not hwnd:
        return
    try:
        user32.ReleaseCapture()
        user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HT.get(edge, HT["br"]), 0)
    except Exception as exc:
        log.warning("native_resize 失败: %s", exc)


def find_hwnd(title_hint: str = "") -> int:
    """按进程 + 标题枚举顶层窗口，拿到 WinForms 窗体句柄。"""
    pid = os.getpid()
    found: list[tuple[int, str, str]] = []
    CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _cb(hwnd, _lparam):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        user32.GetWindowTextW(hwnd, buf, n + 2)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        found.append((int(hwnd), buf.value, cls.value))
        return True

    try:
        user32.EnumWindows(CB(_cb), 0)
    except Exception as exc:
        log.warning("EnumWindows 失败: %s", exc)
        return 0

    best, best_score = 0, -1
    for hwnd, title, cls in found:
        score = 0
        if title_hint and title_hint in title:
            score += 10
        if "WindowsForms" in cls:
            score += 5
        if cls in ("ConsoleWindowClass", "GDI+ Hook Window Class", "IME"):
            score -= 10
        if score > best_score:
            best, best_score = hwnd, score
    return best if best_score > 0 else 0


def drag_async(hwnd: int) -> None:
    """直接走光标轮询拖动：不依赖 WM_NCLBUTTONDOWN 的模态循环，跨线程更可靠。"""
    if not hwnd:
        return
    threading.Thread(target=poll_drag, args=(hwnd,), daemon=True).start()


def resize_async(hwnd: int, edge: str) -> None:
    if not hwnd:
        return
    threading.Thread(target=poll_resize, args=(hwnd, edge), daemon=True).start()


def set_topmost(hwnd: int, on: bool) -> None:
    if not hwnd:
        return
    user32.SetWindowPos(
        hwnd,
        HWND_TOPMOST if on else HWND_NOTOPMOST,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    )


def round_corners(hwnd: int) -> None:
    if not hwnd:
        return
    try:
        val = ctypes.c_int(2)  # DWMWCP_ROUND
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(val), ctypes.sizeof(val)
        )
    except Exception:
        pass


def set_alpha(hwnd: int, alpha: float) -> bool:
    """整窗透明兜底（transparent 模式异常时使用）。"""
    if not hwnd:
        return False
    try:
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
        return bool(user32.SetLayeredWindowAttributes(hwnd, 0, max(0, min(255, int(alpha * 255))), LWA_ALPHA))
    except Exception as exc:
        log.warning("set_alpha 失败: %s", exc)
        return False


def set_toolwindow(hwnd: int, on: bool) -> bool:
    """切换任务栏 / Alt-Tab 可见性（仅保留托盘与价位条时调用）。

    WS_EX_TOOLWINDOW 必须从任务栏移除按钮；同时清掉 WS_EX_APPWINDOW，
    并用 SWP_FRAMECHANGED 通知 Shell 立刻重画任务栏，否则样式要等下次激活才生效。
    """
    if not hwnd:
        return False
    try:
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if on:
            ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        else:
            ex = ex & ~WS_EX_TOOLWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        return True
    except Exception as exc:
        log.warning("set_toolwindow 失败: %s", exc)
        return False


def bring_front(hwnd: int) -> None:
    if not hwnd:
        return
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def poll_resize(hwnd: int, edge: str = "br", min_w: int = 340, min_h: int = 280, fps: int = 60) -> None:
    """原生缩放兜底。"""
    if not hwnd:
        return
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    x0, y0, x1, y1 = get_rect(hwnd)
    w0, h0 = x1 - x0, y1 - y0
    cx, cy = pt.x, pt.y
    while user32.GetAsyncKeyState(0x01) & 0x8000:
        user32.GetCursorPos(ctypes.byref(pt))
        dx, dy = pt.x - cx, pt.y - cy
        w, h, nx, ny = w0, h0, x0, y0
        if "r" in edge:
            w = max(min_w, w0 + dx)
        if "l" in edge:
            w = max(min_w, w0 - dx)
            nx = x1 - w
        if "b" in edge:
            h = max(min_h, h0 + dy)
        if "t" in edge:
            h = max(min_h, h0 - dy)
            ny = y1 - h
        user32.SetWindowPos(hwnd, 0, nx, ny, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
        time.sleep(1 / fps)


def poll_drag(hwnd: int, fps: int = 60) -> None:
    """跟随光标移动窗口（比 WM_NCLBUTTONDOWN 更可控）。"""
    if not hwnd:
        return
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    x0, y0, _, _ = get_rect(hwnd)
    cx, cy = pt.x, pt.y
    log.debug("poll_drag 开始 hwnd=%s at (%s,%s)", hwnd, cx, cy)
    moved = 0
    while user32.GetAsyncKeyState(0x01) & 0x8000:
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetWindowPos(
            hwnd, 0, x0 + (pt.x - cx), y0 + (pt.y - cy), 0, 0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
        )
        moved += 1
        time.sleep(1 / fps)
    log.debug("poll_drag 结束，循环 %s 次", moved)
