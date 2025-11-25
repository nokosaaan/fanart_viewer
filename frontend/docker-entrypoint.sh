#!/bin/sh
set -e

# Create a small runtime env JS that the app can read via window.__ENV
# This allows injecting the backend URL at container start (runtime),
# avoiding the need to set VITE_BACKEND_URL at build time.
ENV_FILE=/usr/share/nginx/html/env.js
echo "Creating runtime env file at $ENV_FILE"
if [ -n "${VITE_BACKEND_URL:-}" ]; then
  cat > "$ENV_FILE" <<EOF
window.__ENV = { VITE_BACKEND_URL: '${VITE_BACKEND_URL}' };
EOF
else
  cat > "$ENV_FILE" <<EOF
window.__ENV = {};
EOF
fi

exec nginx -g 'daemon off;'
