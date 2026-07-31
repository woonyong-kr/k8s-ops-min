#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "usage: $0 <base-revision> <head-revision>" >&2
  exit 2
fi

base_revision="$1"
head_revision="$2"
scope="CONSOLE"
changed_path_count=0
deployable_path_count=0
operational_docs_changed=0
changed_paths_file="$(mktemp)"
trap 'rm -f "${changed_paths_file}"' EXIT

git diff \
  --name-only \
  --no-renames \
  --diff-filter=ACDMRTUXB \
  -z \
  "${base_revision}..${head_revision}" \
  >"${changed_paths_file}"

while IFS= read -r -d '' path; do
  changed_path_count=$((changed_path_count + 1))
  case "${path}" in
    docs/BLOCKERS.md | docs/GOAL-LOG.md | docs/STATUS-REPORT.md)
      operational_docs_changed=1
      continue
      ;;
  esac
  deployable_path_count=$((deployable_path_count + 1))
  if [[ "${path}" != frontend/* ]]; then
    scope="FULL"
  fi
done <"${changed_paths_file}"

if [[ "${changed_path_count}" -eq 0 ]]; then
  scope="FULL"
elif [[ "${deployable_path_count}" -eq 0 && "${operational_docs_changed}" -eq 1 ]]; then
  scope="NONE"
fi

printf '%s\n' "${scope}"
