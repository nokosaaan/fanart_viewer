#!/bin/bash
# Trains the character classifier against this install's own DB, on your
# own schedule -- the packaged build ships with NO pretrained classifier
# at all (a blank "canary" state; see item/management/commands/
# train_character_classifier.py's own docstring), so this is how one
# actually gets grown.
#
# Run from this exe/ directory after building (./build.sh):
#   ./train.sh
# Any extra arguments are forwarded to train_character_classifier as-is,
# e.g.:
#   ./train.sh --min-images 20 --include-multi-character
#
# Progress is tailed live from ~/.fanart_viewer/training.log.
set -e
cd "$(dirname "$0")"

EXE="dist/fanart_viewer/fanart_viewer"
if [ ! -x "$EXE" ]; then
  echo "fanart_viewer not found at $EXE -- build it first with ./build.sh" >&2
  exit 1
fi

LOG="$HOME/.fanart_viewer/training.log"
rm -f "$LOG"
touch "$LOG"

echo "Starting classifier training (log: $LOG)..."
"$EXE" --train-classifier "$@" &
PID=$!

tail -f "$LOG" --pid="$PID" 2>/dev/null &
TAIL_PID=$!

wait "$PID"
CODE=$?
kill "$TAIL_PID" 2>/dev/null || true
echo "Training process exited with code $CODE."
