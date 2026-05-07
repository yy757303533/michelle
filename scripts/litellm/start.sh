#!/usr/bin/env bash
# Launch LiteLLM proxy for Michelle.
#
# Reads FLYWHEEL_TOKEN from Michelle's .env so we don't duplicate secrets.
# Listens on http://localhost:4000.
# Stop with Ctrl+C (or kill the pid).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$REPO_ROOT/scripts/litellm/config.yaml"
ENV_FILE="$REPO_ROOT/.env"

if [[ -f "$ENV_FILE" ]]; then
  # Export FLYWHEEL_TOKEN (and any other key=value pairs) into this shell.
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${FLYWHEEL_TOKEN:-}" ]]; then
  echo "ERROR: FLYWHEEL_TOKEN is not set in $ENV_FILE" >&2
  exit 1
fi

cd "$REPO_ROOT/backend"

unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy SOCKS_PROXY socks_proxy DATABASE_URL
export FLYWHEEL_TOKEN
export NO_PROXY="*"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-600}"

echo "▶ LiteLLM proxy on http://localhost:4000"
echo "  config: $CONFIG"
exec uv run --python 3.12 --with 'litellm[proxy]' \
  litellm --config "$CONFIG" --port 4000 --host 127.0.0.1
