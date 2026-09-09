# Trains the character classifier against this install's own DB, on your
# own schedule -- the exe ships with NO pretrained classifier at all (a
# blank "canary" state; see item/management/commands/train_character_
# classifier.py's own docstring), so this is how one actually gets grown.
#
# Run from this exe\ directory after building (.\build.ps1):
#   .\train.ps1
# Any extra arguments are forwarded to train_character_classifier as-is,
# e.g.:
#   .\train.ps1 --min-images 20 --include-multi-character
#
# Progress is tailed live from %USERPROFILE%\.fanart_viewer\training.log
# (the exe is a windowed build with no console of its own to print to).

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = 'Stop'

$exePath = Join-Path $PSScriptRoot 'dist\fanart_viewer\fanart_viewer.exe'
if (-not (Test-Path $exePath)) {
    Write-Error "fanart_viewer.exe not found at $exePath -- build it first with .\build.ps1"
    exit 1
}

$logPath = Join-Path $env:USERPROFILE '.fanart_viewer\training.log'
Remove-Item $logPath -ErrorAction SilentlyContinue

Write-Host "Starting classifier training (log: $logPath)..."
$proc = Start-Process -FilePath $exePath -ArgumentList (@('--train-classifier') + $ExtraArgs) -PassThru -WindowStyle Hidden

$lastLength = 0
while (-not $proc.HasExited) {
    Start-Sleep -Seconds 1
    if (Test-Path $logPath) {
        $content = Get-Content $logPath -Raw -ErrorAction SilentlyContinue
        if ($content -and $content.Length -gt $lastLength) {
            Write-Host -NoNewline $content.Substring($lastLength)
            $lastLength = $content.Length
        }
    }
}

if (Test-Path $logPath) {
    $content = Get-Content $logPath -Raw -ErrorAction SilentlyContinue
    if ($content -and $content.Length -gt $lastLength) {
        Write-Host -NoNewline $content.Substring($lastLength)
    }
}

Write-Host "`nTraining process exited with code $($proc.ExitCode)."
