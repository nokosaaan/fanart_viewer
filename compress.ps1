# Compress Preview Images and shrink SQLite database
# Run directly from PowerShell in repository root:
#
#   .\compress.ps1 -DryRun   # Simulate compression without modifying DB
#   .\compress.ps1 -Vacuum   # Compress images in DB and run SQLite VACUUM to shrink file

param(
    [switch]$DryRun,
    [switch]$Vacuum,
    [int]$MaxDim = 2048,
    [int]$Quality = 82,
    [string]$DbPath = "$HOME\.fanart_viewer\db.sqlite3",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot

$env:DB_ENGINE = "sqlite3"
if ($DbPath -and (Test-Path $DbPath)) {
    $env:SQLITE_PATH = $DbPath
    Write-Host "Using database: $DbPath"
} elseif (Test-Path "$RepoRoot\backend\data\db.sqlite3") {
    $env:SQLITE_PATH = "$RepoRoot\backend\data\db.sqlite3"
    Write-Host "Using database: $RepoRoot\backend\data\db.sqlite3"
} else {
    Write-Host "Notice: No SQLite file found at $DbPath, using default settings path."
}

$cmdArgs = @("backend/manage.py", "compress_previews", "--max-dim", "$MaxDim", "--quality", "$Quality")
if ($DryRun) {
    $cmdArgs += "--dry-run"
}
if ($Vacuum) {
    $cmdArgs += "--vacuum"
}
if ($ExtraArgs) {
    $cmdArgs += $ExtraArgs
}

python @cmdArgs
