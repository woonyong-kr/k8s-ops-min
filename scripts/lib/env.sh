#!/usr/bin/env bash

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "${name} is required" >&2
    exit 1
  fi
}

generate_password() {
  openssl rand -base64 24 | tr -d '=+/[:space:]' | cut -c1-24
}
