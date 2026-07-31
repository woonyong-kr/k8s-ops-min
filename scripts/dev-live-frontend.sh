#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5173}"
BACKEND_ORIGIN="${BACKEND_ORIGIN:-https://k8s.woonyong.org}"

if [[ "${BACKEND_ORIGIN}" != https://* ]]; then
  echo "BACKEND_ORIGIN must use https: ${BACKEND_ORIGIN}" >&2
  exit 2
fi

if ! curl --max-time 8 --fail --silent --show-error \
  "${BACKEND_ORIGIN%/}/api/healthz" >/dev/null; then
  echo "Kyro live backend is unavailable: ${BACKEND_ORIGIN%/}/api/healthz" >&2
  exit 3
fi

export VITE_BACKEND_ORIGIN="${BACKEND_ORIGIN%/}"
export VITE_DEV_CACHE_DIR="${VITE_DEV_CACHE_DIR:-${ROOT_DIR}/frontend/node_modules/.vite-${PORT}}"
if [[ ! -x "${ROOT_DIR}/frontend/node_modules/.bin/vite" ]] \
  || ! npm --prefix "${ROOT_DIR}/frontend" ls --depth=0 --silent >/dev/null 2>&1; then
  echo "Frontend dependencies are missing or out of sync; installing the locked dependency set."
  npm --prefix "${ROOT_DIR}/frontend" ci --include=dev --no-audit --no-fund
fi

DISPLAY_HOST="${HOST}"
if [[ "${HOST}" == "0.0.0.0" ]]; then
  DISPLAY_HOST="localhost"
fi

echo "Kyro dev frontend: http://${DISPLAY_HOST}:${PORT}"
echo "Live backend: ${VITE_BACKEND_ORIGIN}"
echo "Isolated Vite cache: ${VITE_DEV_CACHE_DIR}"

cd "${ROOT_DIR}/frontend"
exec npm exec -- vite \
  --host "${HOST}" \
  --port "${PORT}" \
  --strictPort
