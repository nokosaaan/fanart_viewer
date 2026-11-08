#!/bin/bash
# Builds the standalone fanart_viewer executable via PyInstaller.
#
# NOTE: PyInstaller cannot cross-compile — running this on Linux produces
# a Linux ELF binary (useful for feasibility-testing the packaging
# approach itself), not a Windows .exe. An actual .exe requires running
# this same script on a Windows machine (or a Windows CI runner) with the
# same Python venv/dependencies installed there.
set -e
cd "$(dirname "$0")"

CLEAN=0
FULL=0
UPX=0

for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=1 ;;
        --full)  FULL=1 ;;
        --upx)   UPX=1 ;;
    esac
done

DIST_APP_DIR="dist/fanart_viewer"
EXE_PATH="$DIST_APP_DIR/fanart_viewer"
INTERNAL_DIR="$DIST_APP_DIR/_internal"
FAST_SYNC_STAMP="$INTERNAL_DIR/.fast_sync_ready"

sync_app_code_and_frontend() {
    echo "backend (backend, item, security) と frontend/dist を _internal/ に同期しています..."
    for pkg in backend item security; do
        if [ -d "../backend/$pkg" ]; then
            if command -v rsync >/dev/null 2>&1; then
                rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' "../backend/$pkg/" "$INTERNAL_DIR/$pkg/"
            else
                rm -rf "$INTERNAL_DIR/$pkg"
                cp -r "../backend/$pkg" "$INTERNAL_DIR/$pkg"
                find "$INTERNAL_DIR/$pkg" -name '__pycache__' -o -name '*.pyc' -exec rm -rf {} + 2>/dev/null || true
            fi
        fi
    done
    if [ -d "../frontend/dist" ]; then
        if command -v rsync >/dev/null 2>&1; then
            rsync -a --delete "../frontend/dist/" "$INTERNAL_DIR/frontend_dist/"
        else
            rm -rf "$INTERNAL_DIR/frontend_dist"
            cp -r "../frontend/dist" "$INTERNAL_DIR/frontend_dist"
        fi
    fi
}

if [ "$CLEAN" -eq 1 ]; then
    echo "クリーンビルド: build および dist を削除しています..."
    rm -rf build dist
fi

CAN_FAST_SYNC=0
if [ "$CLEAN" -eq 0 ] && [ "$FULL" -eq 0 ] && [ "$UPX" -eq 0 ] && [ -f "$EXE_PATH" ] && [ -f "$FAST_SYNC_STAMP" ]; then
    CAN_FAST_SYNC=1
    for tf in splash.png requirements.txt ../backend/requirements.txt ../backend/requirements-timm.txt; do
        if [ -e "$tf" ] && [ "$tf" -nt "$FAST_SYNC_STAMP" ]; then
            CAN_FAST_SYNC=0
            echo "$(basename "$tf") の更新を検出したため、PyInstaller フルビルドを実行します..."
            break
        fi
    done
fi

if [ "$CAN_FAST_SYNC" -eq 1 ]; then
    echo "高速差分同期ビルド: ベース実行ファイルは最新です。変更ファイルのみ反映します (--full で PyInstaller 強制実行)..."
    sync_app_code_and_frontend
    echo "Fast sync completed: $(pwd)/$EXE_PATH"
    exit 0
fi

if [ "$CLEAN" -eq 0 ]; then
    echo "キャッシュを保持して差分 PyInstaller ビルドを実行します (--clean を指定すると全消去)..."
fi

if [ "$UPX" -eq 1 ]; then
    export ENABLE_UPX=1
    echo "UPX 圧縮: 有効"
else
    export ENABLE_UPX=0
fi

# Built from the checked-in fanart_viewer.spec, not plain CLI flags — see
# that file's header comment for why (collect_submodules('item.migrations')
# needs backend/ on sys.path before it runs, which only the spec controls).
pyinstaller --noconfirm fanart_viewer.spec

sync_app_code_and_frontend
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$FAST_SYNC_STAMP"

echo "Built: $(dirname "$0")/dist/fanart_viewer/fanart_viewer (onedir -- ship the whole fanart_viewer/ folder, not just this file)"
