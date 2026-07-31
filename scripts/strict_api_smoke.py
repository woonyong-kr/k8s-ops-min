from __future__ import annotations

import argparse
import http.cookiejar
import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


def require_list(mapping: dict[str, Any], key: str, label: str) -> list[Any]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise RuntimeError(f"{label}.{key} must be a list")
    return value


def require_exact_text(mapping: dict[str, Any], key: str, expected: str, label: str) -> None:
    if mapping.get(key) != expected:
        raise RuntimeError(f"{label}.{key} does not match the smoke fixture")


def normalized_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base URL must not contain credentials")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def smoke_urls(base_url: str, correlation_id: str, incident_id: str) -> dict[str, str]:
    if not correlation_id or not incident_id:
        raise ValueError("correlation and incident fixtures must be non-empty")
    base = normalized_base_url(base_url)
    correlation_path = urllib.parse.quote(correlation_id, safe="")
    incident_path = urllib.parse.quote(incident_id, safe="")
    audit_query = urllib.parse.urlencode({"correlation_id": correlation_id, "limit": 1})
    return {
        "bundle": f"{base}/rca/bundles/{correlation_path}",
        "audit": f"{base}/audit/timeline?{audit_query}",
        "recent": f"{base}/rca/incidents/{incident_path}/recent-changes?limit=1",
    }


def load_cookie_jar(path: Path) -> http.cookiejar.MozillaCookieJar:
    if not path.is_file():
        raise RuntimeError("authenticated cookie jar is missing")
    jar = http.cookiejar.MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    if not list(jar):
        raise RuntimeError("authenticated cookie jar is empty")
    return jar


def fetch_json(url: str, cookie_jar: http.cookiejar.CookieJar) -> Any:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with opener.open(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"strict API smoke expected HTTP 200, got {response.status}")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise RuntimeError("strict API smoke response exceeded 2 MiB")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("strict API smoke response was not JSON") from exc


def validate_bundle(value: Any, correlation_id: str, incident_id: str) -> None:
    bundle = require_mapping(value, "bundle")
    meta = require_mapping(bundle.get("meta"), "bundle.meta")
    require_exact_text(meta, "correlation_id", correlation_id, "bundle.meta")
    require_exact_text(meta, "incident_id", incident_id, "bundle.meta")
    if not isinstance(meta.get("cluster_id"), str) or not meta["cluster_id"]:
        raise RuntimeError("bundle.meta.cluster_id must be non-empty")
    require_mapping(bundle.get("diagnosis"), "bundle.diagnosis")
    remediation = bundle.get("remediation")
    if remediation is not None:
        require_mapping(remediation, "bundle.remediation")


def validate_audit(value: Any) -> None:
    audit = require_mapping(value, "audit")
    items = require_list(audit, "items", "audit")
    if not items:
        raise RuntimeError("audit.items must contain the smoke correlation journey")
    if type(audit.get("limit")) is not int or audit["limit"] < 1:
        raise RuntimeError("audit.limit must be a positive integer")
    if type(audit.get("has_more")) is not bool:
        raise RuntimeError("audit.has_more must be a boolean")
    first = require_mapping(items[0], "audit.items[0]")
    for key in ("event_id", "subject", "journey_stage"):
        if not isinstance(first.get(key), str) or not first[key]:
            raise RuntimeError(f"audit.items[0].{key} must be non-empty")
    require_mapping(first.get("payload_summary"), "audit.items[0].payload_summary")


def validate_recent(value: Any, incident_id: str) -> None:
    recent = require_mapping(value, "recent changes")
    require_exact_text(recent, "incident_id", incident_id, "recent changes")
    items = require_list(recent, "items", "recent changes")
    if type(recent.get("limit")) is not int or recent["limit"] < 1:
        raise RuntimeError("recent changes.limit must be a positive integer")
    if items:
        first = require_mapping(items[0], "recent changes.items[0]")
        if not isinstance(first.get("event_id"), str) or not first["event_id"]:
            raise RuntimeError("recent changes.items[0].event_id must be non-empty")


def run_smoke(
    *,
    base_url: str,
    correlation_id: str,
    incident_id: str,
    cookie_jar: http.cookiejar.CookieJar,
    fetcher: Callable[[str, http.cookiejar.CookieJar], Any] = fetch_json,
) -> None:
    urls = smoke_urls(base_url, correlation_id, incident_id)
    validate_bundle(fetcher(urls["bundle"], cookie_jar), correlation_id, incident_id)
    validate_audit(fetcher(urls["audit"], cookie_jar))
    validate_recent(fetcher(urls["recent"], cookie_jar), incident_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only authenticated smoke for RCA bundle, audit, and recent changes APIs."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--cookie-jar", type=Path, required=True)
    parser.add_argument("--correlation-id", required=True)
    parser.add_argument("--incident-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_smoke(
        base_url=args.base_url,
        correlation_id=args.correlation_id,
        incident_id=args.incident_id,
        cookie_jar=load_cookie_jar(args.cookie_jar),
    )
    print("strict API smoke passed: bundle, audit timeline, recent changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
