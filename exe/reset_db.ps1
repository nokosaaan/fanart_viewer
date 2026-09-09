# Wipes this install's local database (db.sqlite3) -- for trying out a
# clean first-run state during development/testing before distributing.
#
# NOT needed for actual distribution: %USERPROFILE%\.fanart_viewer lives
# under YOUR OWN home directory, never inside the exe\dist\fanart_viewer\
# folder you hand to someone else, so a recipient already gets a blank DB
# automatically on their own first launch -- nothing to clean for that.
#
# Only removes the DB. Twitter/Pixiv/Google Drive credentials, poller
# settings, the downloaded tagger model, and the Playwright browser are
# left alone (so repeated testing doesn't need re-entering credentials or
# re-downloading anything) -- delete %USERPROFILE%\.fanart_viewer entirely
# by hand if you want a truly from-scratch reset of those too.
#
# Close fanart_viewer.exe before running this -- the DB is deleted only
# if it's not currently open by a running instance.

$ErrorActionPreference = 'Stop'

$dataDir = Join-Path $env:USERPROFILE '.fanart_viewer'
$dbPath = Join-Path $dataDir 'db.sqlite3'

if (-not (Test-Path $dbPath)) {
    Write-Host "No database found at $dbPath -- nothing to reset."
    exit 0
}

Write-Host "This will permanently delete: $dbPath"
$confirm = Read-Host "Type 'yes' to continue"
if ($confirm -ne 'yes') {
    Write-Host "Cancelled."
    exit 1
}

try {
    Remove-Item $dbPath -ErrorAction Stop
    # SQLite's WAL-mode sidecar files, if present.
    Remove-Item "$dbPath-wal" -ErrorAction SilentlyContinue
    Remove-Item "$dbPath-shm" -ErrorAction SilentlyContinue
    Write-Host "Deleted. A fresh, empty database will be created next time fanart_viewer.exe starts."
} catch {
    Write-Error "Could not delete $dbPath -- is fanart_viewer.exe still running? Close it and try again."
    exit 1
}
