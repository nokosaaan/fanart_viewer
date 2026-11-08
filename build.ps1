# One-stop build & update script for fanart_viewer standalone exe.
# Run directly from the repository root (fanart_viewer\):
#
#   .\build.ps1                # Activate venv -> git pull -> npm install/build -> exe\build.ps1
#   .\build.ps1 -NoPull        # Skip git pull (build current local working tree)
#   .\build.ps1 -SkipFrontend  # Skip frontend npm install/build
#   .\build.ps1 -InstallDeps   # Force pip install & npm install even if lockfiles haven't changed
#   .\build.ps1 -Full          # Force full PyInstaller run in exe\build.ps1
#   .\build.ps1 -Clean         # Clean build (wipe exe\build and exe\dist)
#   .\build.ps1 -Upx           # Enable UPX compression

param(
    [switch]$NoPull,
    [switch]$SkipFrontend,
    [switch]$InstallDeps,
    [switch]$Full,
    [switch]$Clean,
    [switch]$Upx
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
$VenvDir = Join-Path $RepoRoot ".venv"
$VenvActivate = Join-Path $VenvDir "Scripts\Activate.ps1"
$FrontendDir = Join-Path $RepoRoot "frontend"
$ExeDir = Join-Path $RepoRoot "exe"

Push-Location $RepoRoot
try {
    # ------------------------------------------------------------------
    # 1. Virtual environment (.venv) setup & activation
    # ------------------------------------------------------------------
    $FirstTimeVenv = -not (Test-Path $VenvActivate)
    if ($FirstTimeVenv) {
        Write-Host "==> [1/4] Creating Python virtual environment (.venv)..." -ForegroundColor Cyan
        python -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create .venv (exit code $LASTEXITCODE)"
        }
    }
    else {
        Write-Host "==> [1/4] Activating .venv..." -ForegroundColor Cyan
    }

    . $VenvActivate

    # ------------------------------------------------------------------
    # 2. git pull & Python dependency check
    # ------------------------------------------------------------------
    $ChangedFiles = @()
    if (-not $NoPull) {
        Write-Host "==> [2/4] Running git pull..." -ForegroundColor Cyan
        $BeforeHead = $null
        try {
            $BeforeHead = (git rev-parse HEAD 2>$null).Trim()
        }
        catch {}

        git pull
        if ($LASTEXITCODE -ne 0) {
            throw "git pull failed (exit code $LASTEXITCODE)"
        }

        $AfterHead = $null
        try {
            $AfterHead = (git rev-parse HEAD 2>$null).Trim()
        }
        catch {}

        if ($BeforeHead -and $AfterHead -and ($BeforeHead -ne $AfterHead)) {
            $ChangedFiles = @(git diff --name-only $BeforeHead $AfterHead)
        }
    }
    else {
        Write-Host "==> [2/4] Skipping git pull (-NoPull specified)..." -ForegroundColor Cyan
    }

    $ReqsChanged = $FirstTimeVenv -or $InstallDeps -or (
        $ChangedFiles | Where-Object { $_ -in @("backend/requirements.txt", "exe/requirements.txt") }
    )
    if ($ReqsChanged) {
        Write-Host "==> Installing/updating Python dependencies in .venv..." -ForegroundColor Cyan
        pip install -r (Join-Path $RepoRoot "backend\requirements.txt")
        if ($LASTEXITCODE -ne 0) { throw "pip install backend\requirements.txt failed" }

        pip install -r (Join-Path $RepoRoot "exe\requirements.txt")
        if ($LASTEXITCODE -ne 0) { throw "pip install exe\requirements.txt failed" }

        pip install --no-deps dghs-imgutils pyrfc6266
        if ($LASTEXITCODE -ne 0) { throw "pip install dghs-imgutils pyrfc6266 failed" }
    }

    # ------------------------------------------------------------------
    # 3. Frontend (npm install & npm run build)
    # ------------------------------------------------------------------
    if (-not $SkipFrontend) {
        Push-Location $FrontendDir
        try {
            $NodeModulesDir = Join-Path $FrontendDir "node_modules"
            $NodeModulesStamp = Join-Path $NodeModulesDir ".package-lock.json"
            $PkgJson = Join-Path $FrontendDir "package.json"
            $PkgLock = Join-Path $FrontendDir "package-lock.json"

            $NeedNpmInstall = $InstallDeps -or (-not (Test-Path $NodeModulesDir)) -or (
                $ChangedFiles | Where-Object { $_ -in @("frontend/package.json", "frontend/package-lock.json") }
            )
            if (-not $NeedNpmInstall -and (Test-Path $NodeModulesStamp)) {
                $stampTime = (Get-Item $NodeModulesStamp).LastWriteTimeUtc
                if (((Test-Path $PkgJson) -and ((Get-Item $PkgJson).LastWriteTimeUtc -gt $stampTime)) -or
                    ((Test-Path $PkgLock) -and ((Get-Item $PkgLock).LastWriteTimeUtc -gt $stampTime))) {
                    $NeedNpmInstall = $true
                }
            }
            elseif (-not (Test-Path $NodeModulesStamp)) {
                $NeedNpmInstall = $true
            }

            if ($NeedNpmInstall) {
                Write-Host "==> [3/4] Running npm install in frontend\..." -ForegroundColor Cyan
                npm install
                if ($LASTEXITCODE -ne 0) {
                    throw "npm install failed (exit code $LASTEXITCODE)"
                }
            }
            else {
                Write-Host "==> [3/4] frontend\node_modules is up to date (skipping npm install)..." -ForegroundColor Cyan
            }

            Write-Host "==> [3/4] Running npm run build in frontend\..." -ForegroundColor Cyan
            npm run build
            if ($LASTEXITCODE -ne 0) {
                throw "npm run build failed (exit code $LASTEXITCODE)"
            }
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Host "==> [3/4] Skipping frontend build (-SkipFrontend specified)..." -ForegroundColor Cyan
    }

    # ------------------------------------------------------------------
    # 4. Run exe\build.ps1
    # ------------------------------------------------------------------
    Write-Host "==> [4/4] Running exe\build.ps1..." -ForegroundColor Cyan
    $ExeBuildScript = Join-Path $ExeDir "build.ps1"
    $BuildParams = @{}
    if ($Clean) { $BuildParams["Clean"] = $true }
    if ($Full)  { $BuildParams["Full"]  = $true }
    if ($Upx)   { $BuildParams["Upx"]   = $true }

    & $ExeBuildScript @BuildParams
}
finally {
    Pop-Location
}
