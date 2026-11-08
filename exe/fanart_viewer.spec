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
import json
import os
import shutil
import sys
from pathlib import Path

# SPECPATH is injected by PyInstaller into the spec's exec namespace (no
# __file__ here — the spec is exec()'d, not imported) and is always this
# file's own directory, regardless of the cwd build.sh runs from.
BACKEND_ROOT = os.path.abspath(os.path.join(SPECPATH, '..', 'backend'))
FRONTEND_DIST_ROOT = os.path.abspath(os.path.join(SPECPATH, '..', 'frontend', 'dist'))
sys.path.insert(0, BACKEND_ROOT)

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.building.datastruct import Tree
from PyInstaller.building.splash import Splash
from PyInstaller.building.utils import _make_clean_directory, process_collected_binary
from PyInstaller.compat import is_win, strict_collect_mode
from PyInstaller. log import logger
from PyInstaller.utils import misc as miscutils
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

# torch/torchvision (requirements-timm.txt's optional canary/timm tagger
# backend -- see item/tagger.py's HAVE_TIMM) are only installed in some
# build venvs at all, hence the import-guard: unlike the packages above,
# collect_submodules()/collect_dynamic_libs() on a package that ISN'T
# installed raises ModuleNotFoundError immediately (spec-parse time), which
# would break the build for anyone NOT opting into the timm extras.
#
# When they ARE installed, plain Analysis() static scanning isn't enough --
# confirmed live: a build with no explicit torchvision collection produced
# "RuntimeError: operator torchvision::nms does not exist" at runtime on
# EVERY tagger call (100% failure), even though the exact same torch/
# torchvision versions imported and ran nms fine unfrozen, in the build
# venv itself. torchvision's own torch.library.register_fake("torchvision
# ::nms") (its _meta_registrations.py, imported transitively via `import
# timm` -> timm/layers/norm_act.py's unconditional `from torchvision.ops.
# misc import FrozenBatchNorm2d`) throws unless torchvision's compiled _C
# extension already loaded successfully first -- which needs ITS OWN
# native DLLs (not just the torch ones collect_submodules() finds via pure-
# Python import scanning) to actually be sitting next to it in the frozen
# build. collect_dynamic_libs() is what actually gathers those.
try:
    import importlib.util
    HAVE_TIMM_TAGGER_DEPS = importlib.util.find_spec('torch') is not None
except Exception:
    HAVE_TIMM_TAGGER_DEPS = False


def _dir_signature(path):
    """Lightweight mtime + entry-count signature for cache invalidation."""
    try:
        entries = list(os.scandir(path))
        max_mtime = max((e.stat().st_mtime for e in entries), default=0.0)
        return [os.path.abspath(path), len(entries), round(max_mtime, 3)]
    except OSError:
        return [os.path.abspath(path), -1, 0.0]


def _compute_spec_cache_fingerprint():
    site_dirs = [
        p for p in sys.path
        if 'site-packages' in p or 'dist-packages' in p
    ]
    tracked_dirs = [
        os.path.join(BACKEND_ROOT, 'item', 'migrations'),
        os.path.join(BACKEND_ROOT, 'item', 'management', 'commands'),
        *site_dirs,
    ]
    tracked_files = [
        os.path.join(SPECPATH, 'requirements.txt'),
        os.path.join(BACKEND_ROOT, 'requirements.txt'),
        os.path.join(BACKEND_ROOT, 'requirements-timm.txt'),
    ]
    file_sigs = []
    for fpath in tracked_files:
        try:
            st = os.stat(fpath)
            file_sigs.append([os.path.abspath(fpath), st.st_size, round(st.st_mtime, 3)])
        except OSError:
            file_sigs.append([os.path.abspath(fpath), -1, 0.0])
    return {
        'cache_version': 2,
        'python': sys.executable,
        'version': sys.version,
        'platform': sys.platform,
        'have_timm': HAVE_TIMM_TAGGER_DEPS,
        'dirs': [_dir_signature(d) for d in tracked_dirs],
        'files': file_sigs,
    }


_SPEC_CACHE_PATH = os.path.join(SPECPATH, 'build', 'spec_collect_cache.json')
_SPEC_FINGERPRINT = _compute_spec_cache_fingerprint()
_cached_collect = None

if os.path.exists(_SPEC_CACHE_PATH):
    try:
        with open(_SPEC_CACHE_PATH, 'r', encoding='utf-8') as _f:
            _loaded = json.load(_f)
        if _loaded.get('fingerprint') == _SPEC_FINGERPRINT:
            _sample_files = [
                src for src, _ in (_loaded.get('datas', [])[:5] + _loaded.get('binaries', [])[:5])
            ]
            if all(os.path.exists(p) for p in _sample_files):
                _cached_collect = _loaded
                logger.info('Using cached collect_submodules/collect_data_files/collect_dynamic_libs from %s', _SPEC_CACHE_PATH)
    except Exception:
        _cached_collect = None

base_hiddenimports = [
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

if _cached_collect is not None:
    hiddenimports = base_hiddenimports + list(_cached_collect['hiddenimports'])
    datas = [tuple(x) for x in _cached_collect['datas']]
    binaries = [tuple(x) for x in _cached_collect['binaries']]
else:
    logger.info('Scanning submodules, data files, and dynamic libraries (will cache to %s)...', _SPEC_CACHE_PATH)
    collected_hidden = []
    collected_hidden += collect_submodules('rest_framework')
    collected_hidden += collect_submodules('item.migrations')
    # gallery_dl dynamically imports submodules in extractor/, downloader/
    # (e.g. gallery_dl.downloader.http), and postprocessor/ (e.g.
    # gallery_dl.postprocessor.metadata for --write-info-json) via __import__,
    # which PyInstaller's static analysis cannot see. Collect all submodules
    # of gallery_dl so both --dump-json and actual file downloads work in the
    # frozen exe.
    collected_hidden += collect_submodules('gallery_dl')
    # call_command('poll_twitter_updates', ...) (launcher.py's in-process
    # poller loop) discovers management commands the same pkgutil-based way
    # MigrationLoader discovers migrations — same silent-failure-in-frozen-
    # build class of bug fixed above for item.migrations.
    collected_hidden += collect_submodules('item.management.commands')
    # item.drive_creds_views only imports this lazily (inside a function, for
    # the Google Drive OAuth consent flow) -- confirmed live that PyInstaller's
    # static analysis doesn't follow it: "google-auth-oauthlib がインストール
    # されていません" (the ImportError fallback message) firing in the built
    # exe despite the package being installed in the build venv. Same
    # dynamic-import-blind-spot class as gallery_dl.extractor/item.migrations
    # above; collect its own submodules plus its two direct dependencies
    # (oauthlib/requests_oauthlib) defensively, since neither has an upstream
    # PyInstaller hook of its own.
    collected_hidden += collect_submodules('google_auth_oauthlib')
    collected_hidden += collect_submodules('oauthlib')
    collected_hidden += collect_submodules('requests_oauthlib')

    # Playwright's driver (Node runtime + JS bundle, item/playwright_setup.py)
    # is plain data, not Python source, so Analysis() won't pick it up on its
    # own — it has to be bundled as `datas`, not `hiddenimports`. This is the
    # ~130MB fixed cost of having Playwright-based fetching available at all;
    # only the actual Chromium *browser* binary (~280MB) is deferred to a
    # first-use download (see playwright_setup.py's own header comment).
    collected_datas = []
    collected_datas += collect_data_files('playwright')
    # pykakasi's kanji/kana romanization tables (item/danbooru_lookup.py's
    # _romaji, used by the キャラ↔Danbooru link resolver) are data files too --
    # same class of bug as playwright's driver above (confirmed live:
    # FileNotFoundError for pykakasi/data/kanwadict4.db in a build built
    # without this).
    collected_datas += collect_data_files('pykakasi')
    collected_datas += collect_data_files('rest_framework')

    collected_binaries = []
    if HAVE_TIMM_TAGGER_DEPS:
        collected_hidden += collect_submodules('torchvision')
        collected_binaries += collect_dynamic_libs('torch')
        collected_binaries += collect_dynamic_libs('torchvision')

    try:
        os.makedirs(os.path.dirname(_SPEC_CACHE_PATH), exist_ok=True)
        with open(_SPEC_CACHE_PATH, 'w', encoding='utf-8') as _f:
            json.dump({
                'fingerprint': _SPEC_FINGERPRINT,
                'hiddenimports': collected_hidden,
                'datas': [list(x) for x in collected_datas],
                'binaries': [list(x) for x in collected_binaries],
            }, _f)
    except Exception as _e:
        logger.warning('Failed to write spec collect cache: %s', _e)

    hiddenimports = base_hiddenimports + collected_hidden
    datas = collected_datas
    binaries = collected_binaries

a = Analysis(
    ['launcher.py'],
    pathex=['../backend'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
    # Collect first-party backend packages as plain .py files under _internal/
    # rather than inside PYZ-00.pyz so build.ps1 / build.sh Fast-Sync mode can
    # update backend code in 1-2s without rebuilding the exe.
    module_collection_mode={
        'backend': 'py',
        'item': 'py',
        'security': 'py',
    },
)
pyz = PYZ(a.pure)

# Splash screen: shown by the bootloader itself (a lightweight bundled Tk
# runtime), before launcher.py's own code even starts running — covers the
# gap the pywebview loading window (launcher.py's _LOADING_HTML) can't:
# process startup + heavy imports (Django, onnxruntime, ...) that happen
# before that window is created. launcher.py updates its text via pyi_splash
# and closes it once ready. text_pos is required to enable the text feature
# at all (see PyInstaller's Splash docs) -- position is near the bottom of
# splash.png (480x300).
splash = Splash(
    'splash.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(20, 260),
    text_size=12,
    text_color='#94a3b8',
    text_default='起動中...',
)

# UPX compression: disabled by default for massive build speedup and to avoid
# antivirus false-positives (especially on large torch/onnx/playwright binaries).
# Pass -Upx to build.ps1 (or ENABLE_UPX=1) to enable.
ENABLE_UPX = os.environ.get('ENABLE_UPX', '0').lower() in ('1', 'true', 'yes')

exe = EXE(
    pyz,
    a.scripts,
    splash,
    exclude_binaries=True,
    name='fanart_viewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=ENABLE_UPX,
    upx_exclude=[],
    # Windowed, not console — the pywebview window in launcher.py is the
    # visible/interactive surface now, and stdout/stderr are redirected to
    # USER_DATA_DIR/server.log (see launcher.py's header comment) since a
    # windowed build has no console to print to at all on Windows.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


class IncrementalCOLLECT(COLLECT):
    """COLLECT subclass that avoids deleting and re-copying thousands of
    unchanged files (multi-GB torch/onnxruntime/playwright binaries) on every
    build. Only changed or newly-added files are copied, and stale files no
    longer in the TOC are pruned."""

    def assemble(self):
        if os.environ.get('CLEAN_COLLECT', '0').lower() in ('1', 'true', 'yes') or not os.path.isdir(self.name):
            _make_clean_directory(self.name)
            incremental = False
        else:
            incremental = True

        logger.info("Building %sCOLLECT %s", "incremental " if incremental else "", self.tocbasename)
        expected_dest_paths = set()
        copied_count = 0
        skipped_count = 0

        for dest_name, src_name, typecode in self.toc:
            if typecode not in {'DEPENDENCY', 'SYMLINK'} and not os.path.exists(src_name):
                if strict_collect_mode:
                    raise ValueError(f"Non-existent resource {src_name}, meant to be collected as {dest_name}!")
                else:
                    logger.warning(
                        "Ignoring non-existent resource %s, meant to be collected as %s", src_name, dest_name
                    )
                    continue
            if os.pardir in os.path.normpath(dest_name).split(os.sep) or os.path.isabs(dest_name):
                raise SystemExit(
                    'ERROR: attempting to store file outside of the dist directory: %r. Aborting.' % dest_name
                )
            if typecode in ("EXECUTABLE", "PKG"):
                dest_path = os.path.join(self.name, dest_name)
            else:
                dest_path = os.path.join(self.name, self.contents_directory or "", dest_name)

            norm_dest = os.path.normcase(os.path.abspath(dest_path))
            expected_dest_paths.add(norm_dest)

            dest_dir = os.path.dirname(dest_path)
            os.makedirs(dest_dir, exist_ok=True)

            if typecode in ('EXTENSION', 'BINARY'):
                src_name = process_collected_binary(
                    src_name,
                    dest_name,
                    use_strip=self.strip_binaries,
                    use_upx=self.upx_binaries,
                    upx_exclude=self.upx_exclude,
                    target_arch=self.target_arch,
                    codesign_identity=self.codesign_identity,
                    entitlements_file=self.entitlements_file,
                    strict_arch_validation=(typecode == 'EXTENSION'),
                )
            if typecode == 'SYMLINK':
                if is_win and os.path.sep == '/':
                    src_name = src_name.replace(os.path.sep, '\\')
                if os.path.lexists(dest_path):
                    try:
                        if os.path.islink(dest_path) and os.readlink(dest_path) == src_name:
                            skipped_count += 1
                            continue
                    except OSError:
                        pass
                    os.unlink(dest_path)
                os.symlink(src_name, dest_path)
                copied_count += 1
            elif typecode != 'DEPENDENCY':
                if not os.path.isfile(src_name):
                    raise ValueError(f"Resource {src_name!r} is not a valid file!")
                src_stat = os.stat(src_name)
                if (
                    incremental
                    and typecode not in ('EXECUTABLE', 'PKG')
                    and os.path.isfile(dest_path)
                    and not os.path.islink(dest_path)
                ):
                    try:
                        dest_stat = os.stat(dest_path)
                        if dest_stat.st_size == src_stat.st_size and dest_stat.st_mtime >= src_stat.st_mtime - 1.0:
                            skipped_count += 1
                            continue
                    except OSError:
                        pass
                shutil.copyfile(src_name, dest_path)
                try:
                    os.utime(dest_path, (src_stat.st_atime, src_stat.st_mtime))
                except OSError:
                    pass
                copied_count += 1

            if (
                typecode in ('EXTENSION', 'BINARY', 'EXECUTABLE')
                or (typecode == 'DATA' and os.access(src_name, os.X_OK))
            ):
                os.chmod(dest_path, 0o755)

        removed_count = 0
        if incremental:
            fast_sync_marker = os.path.normcase(
                os.path.abspath(os.path.join(self.name, self.contents_directory or "", ".fast_sync_ready"))
            )
            for root, dirs, files in os.walk(self.name, topdown=False):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    norm_f = os.path.normcase(os.path.abspath(fpath))
                    if norm_f not in expected_dest_paths and norm_f != fast_sync_marker:
                        try:
                            os.unlink(fpath)
                            removed_count += 1
                        except OSError:
                            pass
                for dname in dirs:
                    dpath = os.path.join(root, dname)
                    if os.path.islink(dpath):
                        norm_d = os.path.normcase(os.path.abspath(dpath))
                        if norm_d not in expected_dest_paths:
                            try:
                                os.unlink(dpath)
                                removed_count += 1
                            except OSError:
                                pass
                    else:
                        try:
                            if not os.listdir(dpath):
                                os.rmdir(dpath)
                        except OSError:
                            pass

        logger.info(
            "Building COLLECT %s completed: copied=%d, skipped_unchanged=%d, removed_stale=%d",
            self.tocbasename, copied_count, skipped_count, removed_count,
        )


# Pass frontend/dist and item/fixtures directly to COLLECT as Trees rather
# than Analysis(datas=...) so rebuilding frontend assets never invalidates
# Analysis-00.toc.
extra_collect_trees = []
if os.path.isdir(FRONTEND_DIST_ROOT):
    extra_collect_trees.append(Tree(FRONTEND_DIST_ROOT, prefix='frontend_dist'))
_item_fixtures_dir = os.path.join(BACKEND_ROOT, 'item', 'fixtures')
if os.path.isdir(_item_fixtures_dir):
    extra_collect_trees.append(Tree(_item_fixtures_dir, prefix=os.path.join('item', 'fixtures')))

# Tor Expert Bundle for Windows standalone package:
# Collected under tor/ only when building on Windows and exe/tor_win/ exists.
# Linux builds never include this directory.
_tor_win_dir = os.path.join(SPECPATH, 'tor_win')
if sys.platform == 'win32' and os.path.isdir(_tor_win_dir):
    _tor_bin_dir = os.path.join(_tor_win_dir, 'tor')
    if os.path.isdir(_tor_bin_dir):
        extra_collect_trees.append(Tree(_tor_bin_dir, prefix='tor'))
    _tor_data_dir = os.path.join(_tor_win_dir, 'data')
    if os.path.isdir(_tor_data_dir):
        extra_collect_trees.append(Tree(_tor_data_dir, prefix=os.path.join('tor', 'data')))

# onedir, not onefile: a single-exe build re-extracts its entire contents
# to a fresh temp dir on EVERY launch (real, noticeable delay before
# anything -- even the splash screen's own Tk runtime -- can show up), and
# doesn't meaningfully protect the code from reverse engineering either
# way (Python bytecode decompiles the same regardless of packaging shape).
# This trades a single .exe for a distributable folder in exchange for
# that startup delay going away entirely.
coll = IncrementalCOLLECT(
    exe,
    a.binaries,
    a.datas,
    splash.binaries,
    *extra_collect_trees,
    strip=False,
    upx=ENABLE_UPX,
    upx_exclude=[],
    name='fanart_viewer',
)
