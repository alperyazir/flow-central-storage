#!/bin/sh
# Replace the __VITE_API_BASE_URL__ sentinel in the compiled JS bundle
# with the value of the API_BASE_URL env var. Lets a single admin-panel
# image work in every environment without rebuilding.
#
# Placed in /docker-entrypoint.d/ so the nginx base image runs it
# automatically before starting nginx.
set -eu

API_BASE_URL="${API_BASE_URL:-}"
TARGET_DIR="/usr/share/nginx/html"
SENTINEL="__VITE_API_BASE_URL__"

if [ -z "$API_BASE_URL" ]; then
  echo "[entrypoint] API_BASE_URL is empty; leaving sentinel in place. Set API_BASE_URL to point the admin panel at an FCS API."
  exit 0
fi

# Strip trailing slash so paths concatenated as `${API_BASE_URL}/...`
# don't end up with a double slash.
API_BASE_URL="${API_BASE_URL%/}"

echo "[entrypoint] Substituting ${SENTINEL} -> ${API_BASE_URL} in ${TARGET_DIR}"

# grep -rl finds every file containing the sentinel — JS, CSS, HTML.
# Vite hashes filenames so we can't hardcode a glob.
files=$(grep -rl "$SENTINEL" "$TARGET_DIR" 2>/dev/null || true)
if [ -z "$files" ]; then
  echo "[entrypoint] No files contain ${SENTINEL}; nothing to substitute."
  exit 0
fi

# Use | as sed delimiter because the URL contains forward slashes.
echo "$files" | while IFS= read -r f; do
  [ -n "$f" ] && sed -i "s|${SENTINEL}|${API_BASE_URL}|g" "$f"
done

echo "[entrypoint] Substitution complete."
