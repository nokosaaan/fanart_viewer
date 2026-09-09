# -*- mode: python ; coding: utf-8 -*-
#
# Checked-in spec file (not auto-generated) — needed because
# collect_submodules() below runs at spec-parse time, BEFORE Analysis()
# applies pathex to sys.path. The auto-generated spec PyInstaller writes
# from plain CLI flags runs collect_submodules('item.migrations') with
# backend/ not yet importable, so it silently returns [] (is_package()
# fails quietly rather than raising) — item's migrations never got
# bundled, and 'item' never appeared in migrate's app list at runtime.
# Adding backend/ to sys.path ourselves, first, fixes it for the same
# reason collect_submodules('rest_framework') always worked: rest_framework
# is already importable via site-packages regardless of pathex.
import os
import sys

# SPECPATH is injected by PyInstaller into the spec's exec namespace (no
# __file__ here — the spec is exec()'d, not imported) and is always this
# file's own directory, regardless of the cwd build.sh runs from.
sys.path.insert(0, os.path.join(SPECPATH, '..', 'backend'))

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = [
    'whitenoise.middleware',
    'backend.settings',
    'backend.urls',
    'backend.cors',
    'backend.pagination',
    'security.auth_middleware',
    'item.apps',
    'item.models',
    'item.urls',
    'item.auth_urls',
]
hiddenimports += collect_submodules('rest_framework')
hiddenimports += collect_submodules('item.migrations')

a = Analysis(
    ['launcher.py'],
    pathex=['../backend'],
    binaries=[],
    datas=[('../frontend/dist', 'frontend_dist')],
    hiddenimports=hiddenimports,
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
    name='fanart_viewer',
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
)
