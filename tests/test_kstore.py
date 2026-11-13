"""kstore 自测：直接运行 `python tests/test_kstore.py`，不依赖 pytest。

测试会临时把 config.BARS_DIR 指到系统临时目录，不会碰真实行情库。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, kstore  # noqa: E402


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="moyu-kstore-") as td:
        config.BARS_DIR = Path(td)
        kstore._mem.clear()

        # sanitize：丢弃非法行、按 t 去重（后到覆盖）、升序
        rows = [
            [100, 1, 2, 0.5, 1.5, 10],
            [100, 1, 3, 0.5, 2.5, 20],   # 同 t，应覆盖上面
            [50, 1, 2, 0.9, 1.1, 5],
            [200, 0, 2, 1, 1, 1],        # 价格 0，丢弃
            [300, 1, 2],                 # 列数不足，丢弃
            ["x", 1, 2, 1, 1, 1],        # 时间非法，丢弃
        ]
        clean = kstore.sanitize(rows)
        check([b[0] for b in clean] == [50, 100], "sanitize 排序/去重失败")
        check(clean[1][4] == 2.5 and clean[1][5] == 20, "同 t 新柱未覆盖旧柱")

        # merge：保留历史、刷新末柱、升序
        old = [[100, 1, 2, 0.8, 1.5, 10], [200, 2, 3, 1.8, 2.5, 20]]
        new = [[200, 2, 3.2, 1.8, 3.0, 25], [300, 3, 4, 2.8, 3.5, 30]]
        merged = kstore.merge(old, new)
        check([b[0] for b in merged] == [100, 200, 300], "merge 顺序错误")
        check(merged[1][4] == 3.0, "merge 未刷新同 t 末柱")

        # 文件名可反解 + 区分大小写
        p = kstore.path_for("gf:BTC_USDT", "15m")
        check(p.name.startswith("gf%3ABTC_USDT__15m__"), "文件名格式错误")
        check(kstore.path_for("tx:usAAPL.OQ", "1d") != kstore.path_for("tx:usaapl.oq", "1d"), "大小写未区分")

        # save / load / touch
        check(kstore.save("gf:BTC_USDT", "15m", "gf", merged, at=1000.0), "save 失败")
        got = kstore.load("gf:BTC_USDT", "15m")
        check(got is not None and len(got["bars"]) == 3, "load 失败")
        check(got["provider"] == "gf" and got["at"] == 1000.0, "load 元信息错误")
        kstore.touch("gf:BTC_USDT", "15m", 2000.0)
        check(kstore._mem["gf:BTC_USDT|15m"]["at"] == 2000.0, "touch 失败")

        # needs_write
        check(kstore.needs_write(got, got["bars"], 3000.0) is False, "未变化不应写盘")
        check(kstore.needs_write(got, got["bars"] + [[400, 1, 1, 1, 1, 1]], 3000.0), "新柱应写盘")
        check(kstore.needs_write(got, got["bars"], 1000.0 + 7 * 3600), "超 6h 应写盘")

        # extend：往回补一页后落盘，且保留原有 at（不把历史补写当成尾部刷新）
        kstore._mem.clear()
        kstore.save("gf:EXT_USDT", "1h", "gf", [[200, 1, 2, 0.8, 1.5, 10],
                                              [300, 2, 3, 1.8, 2.5, 20]], at=1234.0)
        ext = kstore.extend("gf:EXT_USDT", "1h", "gf", [[100, 0.5, 1.5, 0.4, 1.2, 5]])
        check([b[0] for b in ext] == [100, 200, 300], "extend 合并顺序错误")
        check(kstore.load("gf:EXT_USDT", "1h")["at"] == 1234.0, "extend 不应改写 at")
        kstore._mem.clear()
        reread = kstore.load("gf:EXT_USDT", "1h")
        check(reread is not None and [b[0] for b in reread["bars"]] == [100, 200, 300],
              "extend 未持久化到磁盘")
        check(kstore.depth("gf:EXT_USDT", "1h") == 3, "depth 错误")
        kstore.path_for("gf:EXT_USDT", "1h").unlink(missing_ok=True)
        kstore._mem.clear()

        # prune：保留最新的 keep 个，但保护收藏
        kstore._mem.clear()
        kstore.path_for("gf:BTC_USDT", "15m").unlink(missing_ok=True)  # 清掉前面的测试文件
        now = time.time()
        symbols = ["gf:A_USDT", "gf:B_USDT", "gf:C_USDT", "gf:D_USDT"]
        for i, s in enumerate(symbols):
            kstore.save(s, "1d", "gf", merged, at=now)
            os.utime(kstore.path_for(s, "1d"), (now - 100 + i, now - 100 + i))
        removed = kstore.prune(protect=["gf:A_USDT"], keep=1)
        check(removed >= 1, "prune 未删除文件")
        check(kstore.path_for("gf:A_USDT", "1d").exists(), "收藏文件被误删")
        check(kstore.path_for("gf:D_USDT", "1d").exists(), "最新文件被误删")

        print("kstore OK: sanitize / merge / save / load / needs_write / extend / prune")


if __name__ == "__main__":
    main()
