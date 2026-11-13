"""系统托盘：常驻通知区，左键唤起/隐藏，右键菜单（收起为滚动条 / 退出）。"""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger("tray")


class Tray:
    def __init__(self, on_toggle: Callable[[], None], on_strip: Callable[[], None],
                 on_quit: Callable[[], None], title: str = "摸鱼看盘", icon_path: str = "") -> None:
        self.on_toggle = on_toggle
        self.on_strip = on_strip
        self.on_quit = on_quit
        self.title = title
        self.icon_path = icon_path
        self._icon = None
        self._tip = title
        self._ready = threading.Event()

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        try:
            import pystray
        except Exception as exc:
            log.warning("托盘库不可用（%s），跳过托盘", exc)
            return False

        image = self._image()
        if image is None:
            return False

        menu = pystray.Menu(
            pystray.MenuItem("显示 / 隐藏窗口", self._toggle, default=True),
            pystray.MenuItem("收起为滚动条", self._strip),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出摸鱼看盘", self._quit),
        )
        try:
            self._icon = pystray.Icon("moyu-kanpan", image, self._tip, menu)
            self._icon.run_detached()
            self._ready.set()
            log.info("托盘图标已就绪")
            return True
        except Exception as exc:
            log.warning("托盘启动失败: %s", exc)
            self._icon = None
            return False

    def stop(self) -> None:
        icon, self._icon = self._icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

    # ---------- 提示文字 ----------
    def set_tip(self, text: str) -> None:
        text = (text or self.title)[:120]
        if text == self._tip:
            return
        self._tip = text
        icon = self._icon
        if icon is None:
            return
        try:
            icon.title = text
        except Exception:
            pass

    # ---------- 回调（pystray 线程） ----------
    def _toggle(self, *_a) -> None:
        try:
            self.on_toggle()
        except Exception as exc:
            log.warning("托盘切换窗口失败: %s", exc)

    def _strip(self, *_a) -> None:
        try:
            self.on_strip()
        except Exception as exc:
            log.warning("托盘收起失败: %s", exc)

    def _quit(self, *_a) -> None:
        try:
            self.stop()
        finally:
            try:
                self.on_quit()
            except Exception as exc:
                log.warning("托盘退出失败: %s", exc)

    # ---------- 图标 ----------
    def _image(self):
        try:
            from PIL import Image
        except Exception as exc:
            log.warning("Pillow 不可用（%s），跳过托盘", exc)
            return None
        if self.icon_path:
            try:
                img = Image.open(self.icon_path)
                img.load()
                return img.resize((64, 64)) if img.size[0] < 32 else img
            except Exception as exc:
                log.debug("读取图标失败 %s: %s", self.icon_path, exc)
        # 兜底：画一个极简蜡烛
        try:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            from PIL import ImageDraw

            d = ImageDraw.Draw(img)
            d.rounded_rectangle([2, 2, 62, 62], radius=14, fill=(255, 255, 255, 255),
                                outline=(206, 212, 220, 255), width=3)
            for cx, top, bot, bt, bb in ((20, 14, 50, 24, 42), (32, 10, 44, 18, 34), (44, 22, 54, 30, 46)):
                d.line([(cx, top), (cx, bot)], fill=(150, 158, 170, 255), width=3)
                d.rectangle([cx - 5, bt, cx + 5, bb], fill=(178, 186, 197, 255))
            return img
        except Exception:
            return None
