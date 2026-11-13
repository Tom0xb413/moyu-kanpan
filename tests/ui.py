"""桌面窗口交互小工具（仅开发调试用，不参与打包）。

用法：
    python tests/ui.py shot 名字
    python tests/ui.py click 300 200
    python tests/ui.py drag 300 200 -380 0
    python tests/ui.py hotkey ctrl alt m
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path
from ctypes import wintypes as wt

ctypes.windll.shcore.SetProcessDpiAwareness(2)
u = ctypes.windll.user32
CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
TITLE = "摸鱼看盘"


def find(title: str = TITLE) -> tuple[int, int, int, int, int]:
    hits = []

    def cb(h, _):
        n = u.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 2)
        u.GetWindowTextW(h, b, n + 2)
        if b.value.strip() == title:
            p = wt.POINT()
            u.ClientToScreen(h, ctypes.byref(p))
            cr = wt.RECT()
            u.GetClientRect(h, ctypes.byref(cr))
            hits.append((int(h), p.x, p.y, cr.right, cr.bottom))
        return True

    u.EnumWindows(CB(cb), 0)
    if not hits:
        raise SystemExit("找不到窗口：%s" % title)
    return hits[0]


def focus(hwnd: int) -> None:
    u.SetForegroundWindow(hwnd)
    time.sleep(0.35)


def click(x: int, y: int, pause: float = 0.06) -> None:
    u.SetCursorPos(x, y)
    time.sleep(0.12)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(pause)
    u.mouse_event(0x0004, 0, 0, 0, 0)


def drag(x: int, y: int, dx: int, dy: int, steps: int = 22) -> None:
    u.SetCursorPos(x, y)
    time.sleep(0.14)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.09)
    for i in range(1, steps + 1):
        u.SetCursorPos(int(x + dx * i / steps), int(y + dy * i / steps))
        time.sleep(0.016)
    time.sleep(0.08)
    u.mouse_event(0x0004, 0, 0, 0, 0)


def key(vk: int) -> None:
    u.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    u.keybd_event(vk, 0, 2, 0)


def shot(name: str, bbox: tuple[int, int, int, int] | None = None) -> Path:
    from PIL import ImageGrab

    out = Path(__file__).resolve().parent.parent / ".workbuddy" / "shots"
    out.mkdir(parents=True, exist_ok=True)
    if bbox is None:
        _, x, y, w, h = find()
        bbox = (x, y, x + w, y + h)
    p = out / (name + ".png")
    ImageGrab.grab(bbox=bbox, all_screens=True).save(p)
    print(p, bbox)
    return p


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "shot":
        shot(sys.argv[2] if len(sys.argv) > 2 else "shot")
        return
    hwnd, cx, cy, w, h = find()
    focus(hwnd)
    if cmd == "click":
        click(cx + int(sys.argv[2]), cy + int(sys.argv[3]))
    elif cmd == "drag":
        drag(cx + int(sys.argv[2]), cy + int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
    elif cmd == "key":
        key(int(sys.argv[2], 0))
    elif cmd == "hotkey":
        vks = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10}
        keys = [vks.get(k, 0) for k in sys.argv[2:-1]] + [int(sys.argv[-1], 0)]
        for vk in keys:
            u.keybd_event(vk, 0, 0, 0)
            time.sleep(0.03)
        time.sleep(0.06)
        for vk in reversed(keys):
            u.keybd_event(vk, 0, 2, 0)
            time.sleep(0.03)
    print("done")


if __name__ == "__main__":
    main()
