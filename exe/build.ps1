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
#   .\build.ps1            # Fast sync (1-3s) if base build exists, else incremental PyInstaller build
#   .\build.ps1 -Full      # Force PyInstaller run (uses spec collect cache + incremental COLLECT)
#   .\build.ps1 -Clean     # Clean build: wipe build/ and dist/ and rebuild from scratch
#   .\build.ps1 -Upx       # Enable UPX compression (slower build, smaller size)

param(
    [switch]$Clean,
    [switch]$Full,
    [switch]$Upx
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$DistAppDir = Join-Path $PSScriptRoot "dist\fanart_viewer"
$ExePath = Join-Path $DistAppDir "fanart_viewer.exe"
$InternalDir = Join-Path $DistAppDir "_internal"
$FastSyncStamp = Join-Path $InternalDir ".fast_sync_ready"
$BackendRoot = Resolve-Path (Join-Path $PSScriptRoot "..\backend")
$FrontendDist = Join-Path $PSScriptRoot "..\frontend\dist"
$TorWinDir = Join-Path $PSScriptRoot "tor_win"
$TorExe = Join-Path $TorWinDir "tor\tor.exe"

function Ensure-TorWindowsBundle {
    if (Test-Path $TorExe) {
        return
    }
    Write-Host "==> Tor Expert Bundle (Windows x86_64) を準備中..." -ForegroundColor Cyan
    $torUrl = "https://archive.torproject.org/tor-package-archive/torbrowser/14.0.1/tor-expert-bundle-windows-x86_64-14.0.1.tar.gz"
    $tempArchive = Join-Path $env:TEMP "tor-expert-bundle.tar.gz"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $torUrl -OutFile $tempArchive -UseBasicParsing
        New-Item -ItemType Directory -Force -Path $TorWinDir | Out-Null
        # Windows 10/11 built-in tar utility extracts tar.gz natively
        tar -xzf $tempArchive -C $TorWinDir
        if (-not (Test-Path $TorExe)) {
            Write-Warning "tar 解凍後に tor.exe が見つかりませんでした: $TorExe"
        } else {
            Write-Host "==> Tor Expert Bundle の配置が完了しました ($TorExe)" -ForegroundColor Green
        }
    }
    catch {
        Write-Warning "Tor Expert Bundle のダウンロードに失敗しました: $_"
    }
    finally {
        if (Test-Path $tempArchive) {
            Remove-Item -Force $tempArchive -ErrorAction SilentlyContinue
        }
    }
}

function Sync-DirectoryIncremental {
    param(
        [string]$Source,
        [string]$Destination
    )
    if (-not (Test-Path $Source)) {
        return
    }
    # robocopy /MIR mirrors source to destination incrementally (comparing size & timestamp),
    # excluding __pycache__ and .pyc files. Exit codes 0..7 indicate success.
    $null = & robocopy $Source $Destination /MIR /XD "__pycache__" /XF "*.pyc" "*.pyo" /NFL /NDL /NJH /NJS /NP /R:1 /W:1 /MT:8
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed syncing '$Source' -> '$Destination' (exit code $LASTEXITCODE)"
    }
    $global:LASTEXITCODE = 0
}

function Sync-AppCodeAndFrontend {
    Write-Host "Syncing backend packages (backend, item, security) and frontend/dist into _internal/..."
    foreach ($pkg in @("backend", "item", "security")) {
        $srcPkg = Join-Path $BackendRoot $pkg
        $dstPkg = Join-Path $InternalDir $pkg
        Sync-DirectoryIncremental -Source $srcPkg -Destination $dstPkg
    }
    $venvGalleryDl = Join-Path $PSScriptRoot "..\.venv\Lib\site-packages\gallery_dl"
    if (Test-Path $venvGalleryDl) {
        Sync-DirectoryIncremental -Source (Resolve-Path $venvGalleryDl) -Destination (Join-Path $InternalDir "gallery_dl")
    }
    if (Test-Path $FrontendDist) {
        $dstFrontend = Join-Path $InternalDir "frontend_dist"
        Sync-DirectoryIncremental -Source (Resolve-Path $FrontendDist) -Destination $dstFrontend
    }
    if (Test-Path $TorWinDir) {
        $srcTorBin = Join-Path $TorWinDir "tor"
        if (Test-Path $srcTorBin) {
            Sync-DirectoryIncremental -Source $srcTorBin -Destination (Join-Path $InternalDir "tor")
        }
        $srcTorData = Join-Path $TorWinDir "data"
        if (Test-Path $srcTorData) {
            Sync-DirectoryIncremental -Source $srcTorData -Destination (Join-Path $InternalDir "tor\data")
        }
    }
}

Ensure-TorWindowsBundle

if ($Clean) {
    Write-Host "Clean build: removing build and dist directories..."
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
}

# Check whether we can use Fast Sync mode (skipping PyInstaller completely)
$CanFastSync = (-not $Clean) -and (-not $Full) -and (-not $Upx) -and (Test-Path $ExePath) -and (Test-Path $FastSyncStamp)
$RebuildReason = ""

if ($CanFastSync) {
    $stampTime = (Get-Item $FastSyncStamp).LastWriteTimeUtc
    $triggerFiles = @(
        (Join-Path $PSScriptRoot "fanart_viewer.spec"),
        (Join-Path $PSScriptRoot "launcher.py"),
        (Join-Path $PSScriptRoot "splash.png"),
        (Join-Path $PSScriptRoot "requirements.txt"),
        (Join-Path $BackendRoot "requirements.txt"),
        (Join-Path $BackendRoot "requirements-timm.txt"),
        $TorExe
    )
    foreach ($tf in $triggerFiles) {
        if (Test-Path $tf) {
            $itemTime = (Get-Item $tf).LastWriteTimeUtc
            if ($itemTime -gt $stampTime) {
                $CanFastSync = $false
                $RebuildReason = "$(Split-Path $tf -Leaf) was updated"
                break
            }
        }
    }
}

if ($CanFastSync) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Write-Host "Fast sync build: base executable is up to date. Updating backend & frontend files only (-Full to force PyInstaller, -Clean to wipe)..."
    Sync-AppCodeAndFrontend
    $sw.Stop()
    Write-Host ("Fast sync completed in {0:N2}s: $ExePath" -f $sw.Elapsed.TotalSeconds)
    exit 0
}

if ($RebuildReason) {
    Write-Host "Running PyInstaller build ($RebuildReason)..."
}
elseif (-not $Clean) {
    Write-Host "Incremental PyInstaller build: keeping cache for faster build (-Clean to rebuild from scratch)..."
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
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Sync-AppCodeAndFrontend
Set-Content -Path $FastSyncStamp -Value ([DateTime]::UtcNow.ToString("o")) -Encoding UTF8

Write-Host "Built: $ExePath (onedir -- ship the whole fanart_viewer\ folder, not just the .exe)"

