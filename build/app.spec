# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件 exe。用法：pyinstaller build/app.spec"""

from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821  (SPECPATH 由 PyInstaller 注入)

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "frontend"), "frontend"),
        (str(ROOT / "vendor"), "vendor"),
        (str(ROOT / "assets"), "assets"),
    ],
    hiddenimports=[
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "webview.platforms.win32",
        "bottle",
        "proxy_tools",
        "typing_extensions",
        # 系统托盘（pystray 的后端是在函数里动态 import 的，静态分析扫不到）
        "pystray",
        "pystray._base",
        "pystray._win32",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFile",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "pytest",
        "setuptools",
        "pip",
        "numpy",
        "matplotlib",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MoYuKanPan",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "app.ico"),
    version=str(ROOT / "build" / "version_info.txt"),
)
