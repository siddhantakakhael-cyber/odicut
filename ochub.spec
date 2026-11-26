# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for OpenCode Hub (Windows .exe, onedir).

Bundles the web UI and (if present) the OpenCode binary in vendor/.
Run:  pyinstaller ochub.spec --noconfirm --clean
"""
import os
from PyInstaller.utils.hooks import collect_all

datas = [("ochub/web", "ochub/web")]
binaries = []
hiddenimports = [
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "webview.platforms.mshtml",
]

# Bundle the OpenCode binary so the exe is self-contained (no npm needed).
for cand in ("vendor/opencode.exe", "vendor/opencode"):
    if os.path.exists(cand):
        binaries.append((cand, "vendor"))
        break

# pywebview + pythonnet (Windows WebView2 backend) need their data/DLLs collected.
for pkg in ("webview", "pythonnet", "clr_loader"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenCodeHub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI app, no console window
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OpenCodeHub",
)
