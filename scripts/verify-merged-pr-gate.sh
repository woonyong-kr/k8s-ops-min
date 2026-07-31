#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "usage: $0 <base-revision> <head-revision>" >&2
  exit 2
fi

base_revision="$1"
head_revision="$2"
deployment_branch="${DEPLOYMENT_BRANCH:-dev}"
gate_workflow="${GATE_WORKFLOW:-dev-gate.yml}"
repository="${GITHUB_REPOSITORY:-}"

fallback_full() {
  echo "$1; running the full merged gate" >&2
  printf 'FULL\n'
  exit 0
}

[[ "${repository}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || fallback_full "GITHUB_REPOSITORY is unavailable or invalid"

base_sha="$(git rev-parse --verify "${base_revision}^{commit}" 2>/dev/null)" \
  || fallback_full "base revision cannot be resolved"
head_sha="$(git rev-parse --verify "${head_revision}^{commit}" 2>/dev/null)" \
  || fallback_full "head revision cannot be resolved"
git merge-base --is-ancestor "${base_sha}" "${head_sha}" \
  || fallback_full "base revision is not an ancestor of head"

commits=()
while IFS= read -r commit_sha; do
  commits+=("${commit_sha}")
done < <(git rev-list --reverse "${base_sha}..${head_sha}")
(( ${#commits[@]} == 1 )) \
  || fallback_full "merged range is not one squash commit"
commit_sha="${commits[0]}"
first_parent="$(git rev-parse "${commit_sha}^" 2>/dev/null)" \
  || fallback_full "merged commit first parent cannot be resolved"
[[ "${first_parent}" == "${base_sha}" ]] \
  || fallback_full "push base does not match the merged commit first parent"

trust_root_paths=(
  ".github/workflows/dev-gate.yml"
  "scripts/verify-merged-pr-gate.sh"
  "scripts/classify-dev-gate-scope.sh"
  "scripts/classify-live-deploy-scope.sh"
  "scripts/classify-deploy-scope.sh"
  "Makefile"
  ".pre-commit-config.yaml"
  "pyproject.toml"
  "uv.lock"
  "frontend/package.json"
  "frontend/package-lock.json"
)
git diff --quiet "${base_sha}..${commit_sha}" -- "${trust_root_paths[@]}" \
  || fallback_full "gate trust root changed"

pull_requests="$(
  gh api \
    --method GET \
    -H "Accept: application/vnd.github+json" \
    "repos/${repository}/commits/${commit_sha}/pulls"
)" || fallback_full "pull request lookup failed for ${commit_sha}"

pull_request="$(
  jq -ce \
    --arg base_sha "${base_sha}" \
    --arg branch "${deployment_branch}" \
    --arg repository "${repository}" \
    --arg sha "${commit_sha}" \
    '
      [
        .[]
        | select(.state == "closed")
        | select(.merged_at != null)
        | select(.base.ref == $branch)
        | select(.base.sha == $base_sha)
        | select(.base.repo.full_name == $repository)
        | select(.head.repo.full_name == $repository)
        | select(.merge_commit_sha == $sha)
      ]
      | if length == 1 then .[0] else empty end
    ' <<<"${pull_requests}"
)" || fallback_full "commit ${commit_sha} is not one uniquely merged repository pull request"

pull_request_number="$(jq -er '.number' <<<"${pull_request}")"
pull_request_head_sha="$(jq -er '.head.sha' <<<"${pull_request}")"
pull_request_head_branch="$(jq -er '.head.ref' <<<"${pull_request}")"
merged_at="$(jq -er '.merged_at' <<<"${pull_request}")"
[[ "${pull_request_head_sha}" =~ ^[0-9a-f]{40}$ ]] \
  || fallback_full "pull request #${pull_request_number} has an invalid head SHA"

pull_request_head="$(
  gh api \
    --method GET \
    "repos/${repository}/git/commits/${pull_request_head_sha}"
)" || fallback_full "pull request head lookup failed for #${pull_request_number}"
pull_request_tree="$(jq -er '.tree.sha' <<<"${pull_request_head}")" \
  || fallback_full "pull request #${pull_request_number} has no tree proof"
merged_tree="$(git rev-parse "${commit_sha}^{tree}")"
[[ "${pull_request_tree}" == "${merged_tree}" ]] \
  || fallback_full "merged tree differs from the tested pull request tree"

workflow_runs="$(
  gh api \
    --method GET \
    "repos/${repository}/actions/workflows/${gate_workflow}/runs" \
    -f "event=pull_request" \
    -f "status=success" \
    -f "head_sha=${pull_request_head_sha}" \
    -f "per_page=100"
)" || fallback_full "gate lookup failed for pull request #${pull_request_number}"

workflow_run="$(
  jq -ce \
    --arg branch "${pull_request_head_branch}" \
    --arg merged_at "${merged_at}" \
    --arg sha "${pull_request_head_sha}" \
    --arg workflow_path ".github/workflows/${gate_workflow}" \
    '
      [
        .workflow_runs[]
        | select(.event == "pull_request")
        | select(.status == "completed")
        | select(.conclusion == "success")
        | select(.head_sha == $sha)
        | select(.head_branch == $branch)
        | select(.path == $workflow_path)
        | select(.updated_at <= $merged_at)
      ]
      | sort_by(.run_attempt)
      | last // empty
    ' <<<"${workflow_runs}"
)" || fallback_full "pull request #${pull_request_number} has no reusable successful full gate"
workflow_run_id="$(jq -er '.id' <<<"${workflow_run}")"

workflow_jobs="$(
  gh api \
    --method GET \
    "repos/${repository}/actions/runs/${workflow_run_id}/jobs" \
    -f "filter=latest" \
    -f "per_page=100"
)" || fallback_full "gate job lookup failed for pull request #${pull_request_number}"

jq -e '
  def successful($name):
    [.jobs[] | select(
      .name == $name
      and .status == "completed"
      and .conclusion == "success"
    )] | length == 1;
  successful("Commit and deployment proof")
  and successful("Backend and manifest gate")
  and successful("Frontend gate")
  and successful("Full gate")
' >/dev/null <<<"${workflow_jobs}" \
  || fallback_full "pull request #${pull_request_number} is missing a required successful gate job"

printf 'REUSE\n'
