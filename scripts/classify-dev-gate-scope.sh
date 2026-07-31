#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "usage: $0 <base-revision> <head-revision>" >&2
  exit 2
fi

base_revision="$1"
head_revision="$2"
changed_paths_file="$(mktemp)"
trap 'rm -f "${changed_paths_file}"' EXIT

fallback_full() {
  echo "$1; using FULL gate scope" >&2
  printf 'FULL\n'
  exit 0
}

if ! git rev-parse --verify "${base_revision}^{commit}" >/dev/null 2>&1; then
  fallback_full "base revision cannot be resolved"
fi
if ! git rev-parse --verify "${head_revision}^{commit}" >/dev/null 2>&1; then
  fallback_full "head revision cannot be resolved"
fi
if ! git diff \
  --name-only \
  --no-renames \
  --diff-filter=ACDMRTUXB \
  -z \
  "${base_revision}..${head_revision}" \
  >"${changed_paths_file}"; then
  fallback_full "changed paths cannot be read"
fi

changed_path_count=0
backend_changed=0
docs_changed=0
frontend_changed=0
smoke_changed=0

while IFS= read -r -d '' path; do
  changed_path_count=$((changed_path_count + 1))
  lower_path="$(printf '%s' "${path}" | LC_ALL=C tr '[:upper:]' '[:lower:]')"

  case "${path}" in
    docs/BLOCKERS.md | docs/GOAL-LOG.md | docs/STATUS-REPORT.md)
      docs_changed=1
      continue
      ;;
  esac

  case "${path}" in
    frontend/scripts/post-deploy-route-smoke.mjs \
      | frontend/scripts/post-deploy-route-smoke.test.mjs \
      | scripts/post-deploy-smoke.sh \
      | scripts/post-deploy-console-smoke.sh \
      | scripts/post_deploy_read_smoke.sh \
      | scripts/pre-deploy-smoke.sh \
      | scripts/lib/public-edge.sh \
      | scripts/lib/cluster-curl.sh \
      | tests/test_deploy_smoke_phases.py)
      smoke_changed=1
      continue
      ;;
  esac

  case "${path}" in
    .github/* \
      | Makefile \
      | .python-version \
      | .pre-commit-config.yaml \
      | pyproject.toml \
      | uv.lock \
      | Dockerfile \
      | Dockerfile.* \
      | .dockerignore \
      | scripts/* \
      | deploy/* \
      | config/* \
      | references/* \
      | docs/migration/* \
      | docs/spec/frontend/* \
      | src/packages/contracts/* \
      | frontend/package.json \
      | frontend/package-lock.json \
      | frontend/.dockerignore \
      | frontend/.env.* \
      | frontend/.npmrc \
      | frontend/components.json \
      | frontend/nginx.conf \
      | frontend/tsconfig*.json \
      | frontend/vite.config.* \
      | frontend/vitest.config.* \
      | frontend/eslint.config.* \
      | frontend/Dockerfile \
      | frontend/Dockerfile.* \
      | frontend/scripts/* \
      | frontend/src/api/* \
      | frontend/src-tauri/* \
      | tests/test_dev_gate*)
      printf 'FULL\n'
      exit 0
      ;;
  esac

  if [[ "${lower_path}" == frontend/*contract* ]]; then
    printf 'FULL\n'
    exit 0
  fi

  case "${path}" in
    frontend/*)
      frontend_changed=1
      ;;
    src/* | tests/* | bruno/*)
      backend_changed=1
      ;;
    *)
      # 알 수 없는 새 루트는 한쪽 gate를 추측해 생략하지 않는다.
      printf 'FULL\n'
      exit 0
      ;;
  esac
done <"${changed_paths_file}"

if [[ "${changed_path_count}" -eq 0 ]]; then
  fallback_full "change set is empty"
fi
if [[ "${smoke_changed}" -eq 1 ]]; then
  if [[ "${backend_changed}" -eq 1 || "${frontend_changed}" -eq 1 ]]; then
    printf 'FULL\n'
  else
    printf 'SMOKE\n'
  fi
elif [[ "${backend_changed}" -eq 1 && "${frontend_changed}" -eq 1 ]]; then
  printf 'FULL\n'
elif [[ "${backend_changed}" -eq 1 ]]; then
  printf 'BACKEND\n'
elif [[ "${frontend_changed}" -eq 1 ]]; then
  printf 'FRONTEND\n'
elif [[ "${docs_changed}" -eq 1 ]]; then
  printf 'DOCS\n'
else
  fallback_full "change set has no classified product path"
fi
