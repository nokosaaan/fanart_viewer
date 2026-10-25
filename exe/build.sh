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
UPX=0

for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=1 ;;
        --upx)   UPX=1 ;;
    esac
done

if [ "$CLEAN" -eq 1 ]; then
    echo "クリーンビルド: build および dist を削除しています..."
    rm -rf build dist
else
    echo "キャッシュを保持して高速差分ビルドを実行します (--clean を指定すると全消去)..."
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

echo "Built: $(dirname "$0")/dist/fanart_viewer/fanart_viewer (onedir -- ship the whole fanart_viewer/ folder, not just this file)"
