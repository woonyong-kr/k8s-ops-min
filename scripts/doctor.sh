#!/usr/bin/env bash
set -euo pipefail

check() {
  local name="$1"
  if command -v "${name}" >/dev/null 2>&1; then
    printf "ok   %s -> %s\n" "${name}" "$(command -v "${name}")"
  else
    printf "miss %s\n" "${name}"
    return 1
  fi
}

status=0
for cmd in git python3 uv docker kubectl kind helm; do
  check "${cmd}" || status=1
done

if docker info >/dev/null 2>&1; then
  echo "ok   docker daemon"
else
  echo "miss docker daemon is not running"
  status=1
fi

echo
python3 --version || true
uv --version || true
docker --version || true
kubectl version --client=true || true
kind version || true
helm version || true

exit "${status}"
