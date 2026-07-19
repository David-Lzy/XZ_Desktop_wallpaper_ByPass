# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys


project_dir = Path(SPECPATH)
conda_bin = Path(sys.prefix) / 'Library' / 'bin'
conda_runtime_names = (
    'ffi.dll',
    'libcrypto-3-x64.dll',
    'libbz2.dll',
    'liblzma.dll',
    'libmpdec-4.dll',
)
conda_runtimes = [
    (str(conda_bin / name), '.')
    for name in conda_runtime_names
    if (conda_bin / name).is_file()
]


a = Analysis(
    [str(project_dir / 'XZWallpaperBypass.py')],
    pathex=[str(project_dir)],
    binaries=conda_runtimes,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='XZWallpaperBypass',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(project_dir / 'version_info.txt'),
)
