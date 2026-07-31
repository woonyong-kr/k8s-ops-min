#!/usr/bin/env bash
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "${root}"

resolve_base() {
  if [[ -n "${GATE_BASE:-}" ]]; then
    git rev-parse --verify "${GATE_BASE}^{commit}"
    return
  fi

  local upstream
  if upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)"; then
    git merge-base HEAD "${upstream}"
    return
  fi

  if git rev-parse --verify HEAD^ >/dev/null 2>&1; then
    git rev-parse HEAD^
    return
  fi

  git hash-object -t tree /dev/null
}

base="$(resolve_base)"
if [[ "${1:-}" == "--base" ]]; then
  printf '%s\n' "${base}"
  exit 0
fi
if (( $# > 0 )); then
  echo "usage: $0 [--base]" >&2
  exit 2
fi

{
  git diff --name-only --diff-filter=ACMRTUXB "${base}" --
  git ls-files --others --exclude-standard
} | sed '/^$/d' | LC_ALL=C sort -u
