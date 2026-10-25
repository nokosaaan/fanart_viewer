# Builds the standalone fanart_viewer.exe via PyInstaller — the Windows
# equivalent of build.sh. Unlike build.sh (which only produces a Linux
# ELF binary useful for feasibility-testing), running THIS script on
# actual Windows produces a real, distributable fanart_viewer.exe —
# PyInstaller cannot cross-compile, so a Windows build only ever happens
# by running PyInstaller on Windows itself (or a Windows CI runner).
#
# Run from a PowerShell session with the project's venv already active
# and backend/requirements.txt + pyinstaller + waitress installed into
# it (see the one-time setup below). Invoke as:
#   cd <repo>\fanart_viewer\exe
#   .\build.ps1            # キャッシュを使って高速に差分ビルド (通常はこちら)
#   .\build.ps1 -Clean     # build/ と dist/ を全削除して完全クリーンビルド
#   .\build.ps1 -Upx       # UPX圧縮を有効化 (配布サイズ縮小用、ビルド時間は長くなります)
param(
    [switch]$Clean,
    [switch]$Upx
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if ($Clean) {
    Write-Host "クリーンビルド: build および dist ディレクトリを削除しています..." -ForegroundColor Cyan
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
} else {
    Write-Host "キャッシュを保持して高速差分ビルドを実行します (完全再構築は -Clean を指定)..." -ForegroundColor Green
}

# UPX 圧縮の切り替え（デフォルト無効でビルド時間を大幅短縮）
if ($Upx) {
    $env:ENABLE_UPX = "1"
    Write-Host "UPX 圧縮: 有効 (時間がかかります)" -ForegroundColor Yellow
} else {
    $env:ENABLE_UPX = "0"
}

# Built from the checked-in fanart_viewer.spec, not plain CLI flags — see
# that file's header comment for why (collect_submodules('item.migrations')
# needs backend/ on sys.path before it runs, which only the spec controls).
pyinstaller --noconfirm fanart_viewer.spec

Write-Host "Built: $PSScriptRoot\dist\fanart_viewer\fanart_viewer.exe (onedir -- ship the whole fanart_viewer\ folder, not just the .exe)" -ForegroundColor Green
