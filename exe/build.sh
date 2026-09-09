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

rm -rf build dist

# Built from the checked-in fanart_viewer.spec, not plain CLI flags — see
# that file's header comment for why (collect_submodules('item.migrations')
# needs backend/ on sys.path before it runs, which only the spec controls).
pyinstaller fanart_viewer.spec

echo "Built: $(dirname "$0")/dist/fanart_viewer"
