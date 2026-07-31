#!/usr/bin/env bash

AUTH_LOGIN_ATTEMPTS="${AUTH_LOGIN_ATTEMPTS:-12}"
AUTH_LOGIN_RETRY_INTERVAL_SECONDS="${AUTH_LOGIN_RETRY_INTERVAL_SECONDS:-5}"

login_with_password() {
  local base_url="$1"
  local cookie_jar="$2"

  if [ -z "${AUTH_EMAIL:-}" ] || [ -z "${AUTH_PASSWORD:-}" ]; then
    echo "AUTH_EMAIL and AUTH_PASSWORD are required for password login" >&2
    exit 1
  fi

  local login_body
  login_body="$(
    AUTH_EMAIL="${AUTH_EMAIL}" AUTH_PASSWORD="${AUTH_PASSWORD}" python3 - <<'PY'
import json
import os

print(json.dumps({
    "email": os.environ["AUTH_EMAIL"],
    "password": os.environ["AUTH_PASSWORD"],
}))
PY
  )"

  local attempt
  local output=""
  for attempt in $(seq 1 "${AUTH_LOGIN_ATTEMPTS}"); do
    if output="$(curl -fsS -X POST "${base_url}/auth/login" \
      -H "content-type: application/json" \
      -H "x-service-csrf: same-origin" \
      -c "${cookie_jar}" \
      -d "${login_body}" 2>&1 >/dev/null)"; then
      return 0
    fi
    if [ "${attempt}" != "${AUTH_LOGIN_ATTEMPTS}" ]; then
      echo "login failed (attempt ${attempt}/${AUTH_LOGIN_ATTEMPTS}); retrying in ${AUTH_LOGIN_RETRY_INTERVAL_SECONDS}s" >&2
      printf '%s\n' "${output}" >&2
      sleep "${AUTH_LOGIN_RETRY_INTERVAL_SECONDS}"
    fi
  done

  echo "login failed after ${AUTH_LOGIN_ATTEMPTS} attempts" >&2
  printf '%s\n' "${output}" >&2
  return 1
}
