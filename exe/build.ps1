# Builds the standalone fanart_viewer.exe via PyInstaller -- the Windows
# equivalent of build.sh. Unlike build.sh (which only produces a Linux
# ELF binary useful for feasibility-testing), running THIS script on
# actual Windows produces a real, distributable fanart_viewer.exe --
# PyInstaller cannot cross-compile, so a Windows build only ever happens
# by running PyInstaller on Windows itself (or a Windows CI runner).
#
# Run from a PowerShell session with the project's venv already active
# and backend/requirements.txt + pyinstaller + waitress installed into
# it (see the one-time setup below). Invoke as:
#   cd <repo>\fanart_viewer\exe
#   .\build.ps1            # Fast incremental build using PyInstaller cache (default)
#   .\build.ps1 -Clean     # Clean build: wipe build/ and dist/
#   .\build.ps1 -Upx       # Enable UPX compression (slower build, smaller size)

param(
    [switch]$Clean,
    [switch]$Upx
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if ($Clean) {
    Write-Host "Clean build: removing build and dist directories..."
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
}
else {
    Write-Host "Incremental build: keeping cache for faster build (-Clean to rebuild from scratch)..."
}

if ($Upx) {
    $env:ENABLE_UPX = "1"
    Write-Host "UPX compression: ENABLED (build will take longer)"
}
else {
    $env:ENABLE_UPX = "0"
}

# Built from the checked-in fanart_viewer.spec, not plain CLI flags -- see
# that file's header comment for why (collect_submodules('item.migrations')
# needs backend/ on sys.path before it runs, which only the spec controls).
pyinstaller --noconfirm fanart_viewer.spec

Write-Host "Built: $PSScriptRoot\dist\fanart_viewer\fanart_viewer.exe (onedir -- ship the whole fanart_viewer\ folder, not just the .exe)"

