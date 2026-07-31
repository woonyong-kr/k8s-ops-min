#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 <head-revision>" >&2
  exit 2
fi

head_revision="$1"
deployment_branch="${DEPLOYMENT_BRANCH:-dev}"
deployment_workflow="${DEPLOYMENT_WORKFLOW:-dev-deploy.yml}"
git_remote="${GIT_REMOTE:-origin}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

fallback_full() {
  echo "$1; using FULL deployment scope" >&2
  printf 'FULL\n'
  exit 0
}

if [[ -z "${GITHUB_REPOSITORY:-}" ]]; then
  fallback_full "GITHUB_REPOSITORY is unavailable"
fi

if ! head_sha="$(git rev-parse --verify "${head_revision}^{commit}" 2>/dev/null)"; then
  fallback_full "head revision cannot be resolved"
fi

if ! deployed_sha="$(
  gh api \
    --method GET \
    "repos/${GITHUB_REPOSITORY}/actions/workflows/${deployment_workflow}/runs" \
    -f "branch=${deployment_branch}" \
    -f "status=success" \
    -f "per_page=100" \
    --jq '[.workflow_runs[] | select(.event == "push" or .event == "workflow_run")][0].head_sha // empty'
)"; then
  fallback_full "latest successful deployment lookup failed"
fi

if [[ ! "${deployed_sha}" =~ ^[0-9a-f]{40}$ ]]; then
  fallback_full "latest successful deployment SHA is unavailable or invalid"
fi

is_deployed_ancestor() {
  git cat-file -e "${deployed_sha}^{commit}" 2>/dev/null \
    && git merge-base --is-ancestor "${deployed_sha}" "${head_sha}" 2>/dev/null
}

if ! is_deployed_ancestor; then
  if [[ "$(git rev-parse --is-shallow-repository)" == "true" ]]; then
    if ! git fetch \
      --no-tags \
      --filter=blob:none \
      --unshallow \
      "${git_remote}" \
      "${deployment_branch}"; then
      fallback_full "deployment ancestry history fetch failed"
    fi
  elif ! git cat-file -e "${deployed_sha}^{commit}" 2>/dev/null; then
    if ! git fetch \
      --no-tags \
      --filter=blob:none \
      "${git_remote}" \
      "${deployment_branch}"; then
      fallback_full "deployed commit fetch failed"
    fi
  fi
fi

if ! is_deployed_ancestor; then
  fallback_full "latest successful deployment is not an ancestor of HEAD"
fi

"${script_dir}/classify-deploy-scope.sh" "${deployed_sha}" "${head_sha}"
