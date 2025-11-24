#!/usr/bin/env bash
# Helper script to create a .env with TW_BEARER (overwrites .env)
# Usage: ./scripts/set_tw_bearer.sh "<YOUR_BEARER_TOKEN>"

if [ -z "$1" ]; then
  echo "Usage: $0 \"<TW_BEARER_TOKEN>\""
  exit 1
fi
TOKEN="$1"
cat > .env <<EOF
# Auto-generated .env (do NOT commit)
TW_BEARER=${TOKEN}
EOF

echo ".env written (TW_BEARER hidden). You can now restart the web service:"
echo "  docker compose restart web"
