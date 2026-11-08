#!/bin/bash
# One-stop build & update script for fanart_viewer standalone executable.
# Run directly from the repository root (fanart_viewer/):
#
#   ./build.sh                 # Activate venv -> git pull -> npm install/build -> exe/build.sh
#   ./build.sh --no-pull       # Skip git pull
#   ./build.sh --skip-frontend # Skip frontend npm install/build
#   ./build.sh --install-deps  # Force pip install & npm install
#   ./build.sh --full          # Force full PyInstaller run in exe/build.sh
#   ./build.sh --clean         # Clean build (wipe exe/build and exe/dist)
#   ./build.sh --upx           # Enable UPX compression

set -e
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

NO_PULL=0
SKIP_FRONTEND=0
INSTALL_DEPS=0
EXE_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --no-pull)       NO_PULL=1 ;;
        --skip-frontend) SKIP_FRONTEND=1 ;;
        --install-deps)  INSTALL_DEPS=1 ;;
        --clean|--full|--upx) EXE_ARGS+=("$arg") ;;
    esac
done

# 1. Virtual environment (.venv)
FIRST_TIME_VENV=0
if [ ! -f ".venv/bin/activate" ]; then
    echo "==> [1/4] Creating Python virtual environment (.venv)..."
    python3 -m venv .venv
    FIRST_TIME_VENV=1
else
    echo "==> [1/4] Activating .venv..."
fi
# shellcheck disable=SC1091
source ".venv/bin/activate"

# 2. git pull & Python dependencies
BEFORE_HEAD=""
AFTER_HEAD=""
if [ "$NO_PULL" -eq 0 ]; then
    echo "==> [2/4] Running git pull..."
    BEFORE_HEAD="$(git rev-parse HEAD 2>/dev/null || true)"
    git pull
    AFTER_HEAD="$(git rev-parse HEAD 2>/dev/null || true)"
else
    echo "==> [2/4] Skipping git pull (--no-pull specified)..."
fi

REQS_CHANGED=0
if [ "$FIRST_TIME_VENV" -eq 1 ] || [ "$INSTALL_DEPS" -eq 1 ]; then
    REQS_CHANGED=1
elif [ -n "$BEFORE_HEAD" ] && [ -n "$AFTER_HEAD" ] && [ "$BEFORE_HEAD" != "$AFTER_HEAD" ]; then
    if git diff --name-only "$BEFORE_HEAD" "$AFTER_HEAD" | grep -qE '^(backend/requirements\.txt|exe/requirements\.txt)$'; then
        REQS_CHANGED=1
    fi
fi

if [ "$REQS_CHANGED" -eq 1 ]; then
    echo "==> Installing/updating Python dependencies in .venv..."
    pip install -r backend/requirements.txt
    pip install -r exe/requirements.txt
    pip install --no-deps dghs-imgutils pyrfc6266
fi

# 3. Frontend (npm install & npm run build)
if [ "$SKIP_FRONTEND" -eq 0 ]; then
    pushd frontend >/dev/null
    NEED_NPM_INSTALL=0
    if [ "$INSTALL_DEPS" -eq 1 ] || [ ! -d "node_modules" ] || [ ! -f "node_modules/.package-lock.json" ]; then
        NEED_NPM_INSTALL=1
    elif [ "package.json" -nt "node_modules/.package-lock.json" ] || [ "package-lock.json" -nt "node_modules/.package-lock.json" ]; then
        NEED_NPM_INSTALL=1
    fi

    if [ "$NEED_NPM_INSTALL" -eq 1 ]; then
        echo "==> [3/4] Running npm install in frontend/..."
        npm install
    else
        echo "==> [3/4] frontend/node_modules is up to date (skipping npm install)..."
    fi

    echo "==> [3/4] Running npm run build in frontend/..."
    npm run build
    popd >/dev/null
else
    echo "==> [3/4] Skipping frontend build (--skip-frontend specified)..."
fi

# 4. Run exe/build.sh
echo "==> [4/4] Running exe/build.sh..."
./exe/build.sh "${EXE_ARGS[@]}"
