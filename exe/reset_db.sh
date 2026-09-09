#!/bin/bash
# Wipes this install's local database (db.sqlite3) -- for trying out a
# clean first-run state during development/testing before distributing.
#
# NOT needed for actual distribution: ~/.fanart_viewer lives under YOUR
# OWN home directory, never inside the exe/dist/fanart_viewer/ folder you
# hand to someone else, so a recipient already gets a blank DB
# automatically on their own first launch -- nothing to clean for that.
#
# Only removes the DB. Twitter/Pixiv/Google Drive credentials, poller
# settings, the downloaded tagger model, and the Playwright browser are
# left alone (so repeated testing doesn't need re-entering credentials or
# re-downloading anything) -- delete ~/.fanart_viewer entirely by hand if
# you want a truly from-scratch reset of those too.
#
# Close the fanart_viewer process before running this.
set -e

DATA_DIR="$HOME/.fanart_viewer"
DB_PATH="$DATA_DIR/db.sqlite3"

if [ ! -f "$DB_PATH" ]; then
  echo "No database found at $DB_PATH -- nothing to reset."
  exit 0
fi

echo "This will permanently delete: $DB_PATH"
read -p "Type 'yes' to continue: " confirm
if [ "$confirm" != "yes" ]; then
  echo "Cancelled."
  exit 1
fi

rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"
echo "Deleted. A fresh, empty database will be created next time fanart_viewer starts."
