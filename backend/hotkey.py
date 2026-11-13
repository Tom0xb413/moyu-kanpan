"""全局热键：独立线程 RegisterHotKey + 自建消息循环（不碰 WinForms 消息泵）。"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes as wt

log = logging.getLogger("hotkey")

user32 = ctypes.windll.user32
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.RegisterHotKey.restype = wt.BOOL
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]

MOD_ALT = 0x1
MOD_CONTROL = 0x2
MOD_SHIFT = 0x4
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

CANDIDATES = [
    ((MOD_CONTROL | MOD_ALT, 0x48), "Ctrl+Alt+H"),
    ((MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x48), "Ctrl+Alt+Shift+H"),
    ((MOD_CONTROL | MOD_ALT, 0x42), "Ctrl+Alt+B"),
]


class HotkeyThread(threading.Thread):
    def __init__(self, on_trigger) -> None:
        super().__init__(daemon=True, name="hotkey")
        self.on_trigger = on_trigger
        self.registered: list[int] = []
        self.label = ""
        self.ok = False

    def run(self) -> None:
        for idx, (combo, label) in enumerate(CANDIDATES, start=1):
            mods, vk = combo
            try:
                if user32.RegisterHotKey(None, idx, mods | MOD_NOREPEAT, vk):
                    self.registered.append(idx)
                    if not self.label:
                        self.label = label
            except Exception as exc:
                log.warning("注册热键 %s 失败: %s", label, exc)
        if not self.registered:
            log.warning("所有热键组合均被占用，热键功能停用")
            return
        self.ok = True
        log.info("热键已注册：%s", self.label)
        msg = wt.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                try:
                    threading.Thread(target=self.on_trigger, daemon=True).start()
                except Exception:
                    pass
        for idx in self.registered:
            try:
                user32.UnregisterHotKey(None, idx)
            except Exception:
                pass

    def stop(self) -> None:
        try:
            user32.PostThreadMessageW(self.ident, WM_QUIT, 0, 0)
        except Exception:
            pass
