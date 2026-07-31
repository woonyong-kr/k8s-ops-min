#!/usr/bin/env bash

_probe_public_edge_from_management_cluster() {
  local base_url="$1"
  local expected_bundle="$2"
  local source_sha="$3"
  local attempt="$4"

  kubectl --context "${MGMT_CONTEXT}" -n "${MGMT_NS}" \
    exec -i deployment/api-gateway -c gateway -- \
    python - "${base_url}" "${expected_bundle}" "${source_sha}" "${attempt}" <<'PY'
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

base_url, expected_bundle, source_sha, attempt = sys.argv[1:]
parsed = urlsplit(base_url)
if (
    parsed.scheme != "https"
    or not parsed.netloc
    or parsed.username is not None
    or parsed.password is not None
):
    raise SystemExit("public edge base URL must be credential-free HTTPS")


def release_url(path: str, query: dict[str, str] | None = None) -> str:
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            urlencode(query or {}),
            "",
        )
    )


def fetch(url: str, *, limit: int) -> tuple[int, bytes]:
    request = Request(
        url,
        headers={
            "Cache-Control": "no-cache",
            "User-Agent": "Opsia-Release-Probe/1.0",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read(limit + 1)
            if len(body) > limit:
                return int(response.status), b""
            return int(response.status), body
    except HTTPError as exc:
        return int(exc.code), b""
    except (OSError, TimeoutError, URLError):
        return 0, b""


health_status, health_body = fetch(release_url("/api/healthz"), limit=1_048_576)
health_valid = False
if health_status == 200:
    try:
        health = json.loads(health_body)
        health_valid = (
            health.get("status") == "ok"
            and health.get("service") == "api-gateway"
        )
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        health_valid = False

index_status, index_body = fetch(
    release_url(
        "/",
        {
            "source_sha": source_sha,
            "edge_attempt": attempt,
        },
    ),
    limit=4_194_304,
)
match = re.search(rb"index-[A-Za-z0-9_-]+\.js", index_body)
observed_bundle = match.group(0).decode("ascii") if match else ""
release_bundle = expected_bundle or observed_bundle

bundle_status = 0
source_valid = False
if re.fullmatch(r"index-[A-Za-z0-9_-]+\.js", release_bundle):
    bundle_status, bundle_body = fetch(
        release_url(
            f"/assets/{quote(release_bundle, safe='')}",
            {"source_sha": source_sha},
        ),
        limit=33_554_432,
    )
    source_valid = bundle_status == 200 and source_sha.encode("ascii") in bundle_body

print(
    json.dumps(
        {
            "health_status": health_status,
            "health_valid": health_valid,
            "index_status": index_status,
            "observed_bundle": observed_bundle,
            "release_bundle": release_bundle,
            "bundle_status": bundle_status,
            "source_valid": source_valid,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
)
PY
}

wait_for_public_edge_release() {
  local base_url="$1"
  local expected_bundle="$2"
  local source_sha="$3"
  local max_attempts="${PUBLIC_EDGE_MAX_ATTEMPTS:-30}"
  local retry_seconds="${PUBLIC_EDGE_RETRY_SECONDS:-2}"
  local attempt
  local probe_result
  local probe_json
  local probe_exit
  local probe_error_file
  local probe_error_bytes
  local probe_contract
  local health_status
  local index_status
  local bundle_status
  local observed_bundle
  local release_bundle

  [[ "${base_url}" =~ ^https://[^[:space:]]+$ ]]
  [[ -z "${expected_bundle}" ]] || [[ "${expected_bundle}" =~ ^index-[A-Za-z0-9_-]+\.js$ ]]
  [[ "${source_sha}" =~ ^[0-9a-f]{40}$ ]]
  [[ "${max_attempts}" =~ ^[1-9][0-9]*$ ]]
  [[ "${retry_seconds}" =~ ^[0-9]+([.][0-9]+)?$ ]]
  [[ -n "${MGMT_CONTEXT:-}" ]]
  [[ -n "${MGMT_NS:-}" ]]
  command -v kubectl >/dev/null 2>&1
  command -v jq >/dev/null 2>&1

  probe_error_file="$(mktemp)"
  for attempt in $(seq 1 "${max_attempts}"); do
    if probe_result="$(
      _probe_public_edge_from_management_cluster \
        "${base_url}" "${expected_bundle}" "${source_sha}" "${attempt}" \
        2>"${probe_error_file}"
    )"; then
      probe_exit=0
    else
      probe_exit=$?
    fi
    probe_json='{}'
    if [[ -n "${probe_result}" ]] && jq -e 'type == "object"' \
        <<<"${probe_result}" >/dev/null 2>&1; then
      probe_json="${probe_result}"
      probe_contract="valid-json"
    else
      probe_contract="invalid-json"
    fi
    probe_error_bytes="$(wc -c <"${probe_error_file}" | tr -d '[:space:]')"

    health_status="$(jq -r '.health_status // 0' <<<"${probe_json}")"
    index_status="$(jq -r '.index_status // 0' <<<"${probe_json}")"
    bundle_status="$(jq -r '.bundle_status // 0' <<<"${probe_json}")"
    observed_bundle="$(
      jq -r '.observed_bundle // ""' <<<"${probe_json}"
    )"
    release_bundle="$(
      jq -r '.release_bundle // ""' <<<"${probe_json}"
    )"

    if jq -e \
      --arg expected_bundle "${expected_bundle}" \
      '
        .health_status == 200
        and .health_valid == true
        and .index_status == 200
        and (.release_bundle | test("^index-[A-Za-z0-9_-]+[.]js$"))
        and ($expected_bundle == "" or .observed_bundle == $expected_bundle)
        and .bundle_status == 200
        and .source_valid == true
      ' <<<"${probe_json}" >/dev/null 2>&1; then
      printf 'public edge converged: attempt=%s health=200 bundle=%s\n' \
        "${attempt}" "${release_bundle}"
      rm -f -- "${probe_error_file}"
      return 0
    fi

    printf 'public edge pending: attempt=%s/%s health=%s index=%s bundle=%s observed=%s probe_exit=%s\n' \
      "${attempt}" "${max_attempts}" "${health_status}" "${index_status}" \
      "${bundle_status}" "${observed_bundle:-none}" "${probe_exit}" >&2
    if (( attempt < max_attempts )); then
      sleep "${retry_seconds}"
    fi
  done

  printf 'public edge probe diagnostic: kubectl_exec_exit=%s response=%s stderr_bytes=%s\n' \
    "${probe_exit}" "${probe_contract}" "${probe_error_bytes}" >&2
  rm -f -- "${probe_error_file}"
  echo "public edge failed to converge to ${source_sha} / ${expected_bundle:-discovered-bundle}" >&2
  return 1
}
