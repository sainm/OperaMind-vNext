# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata


ROOT = Path(SPECPATH).parent
RESOURCE_DIRECTORIES = (
    "contracts",
    "golden-dataset",
    "migrations",
    "profiles",
    "readiness",
)
datas = [(str(ROOT / name), name) for name in RESOURCE_DIRECTORIES]
datas += copy_metadata("openpyxl")
datas += copy_metadata("python-docx")
datas.append((str(ROOT / "src" / "operamind" / "web" / "static"), "operamind/web/static"))
datas.append((str(ROOT / "vscode-extension" / "package.json"), "vscode-extension"))

analysis = Analysis(
    [str(ROOT / "src" / "operamind" / "commands" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

if sys.platform == "darwin":
    exe = EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name="OperaMind",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    collected = COLLECT(
        exe,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=True,
        name="OperaMind",
    )
    app = BUNDLE(
        collected,
        name="OperaMind.app",
        icon=None,
        bundle_identifier="local.operamind.desktop",
        info_plist={
            "CFBundleDisplayName": "OperaMind",
            "LSBackgroundOnly": False,
            "NSHighResolutionCapable": True,
        },
    )
else:
    exe = EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name="OperaMind",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
    )
    mcp_exe = EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name="OperaMindMcp",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console="hide-early",
        disable_windowed_traceback=False,
    )
