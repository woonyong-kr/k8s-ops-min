#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API_BASE = "https://api.cloudflare.com/client/v4"
HOSTNAME = "dev-k8s.woonyong.org"
ZONE_NAME = "woonyong.org"
WAF_PHASE = "http_request_firewall_custom"
WAF_RULE_REF = "require_mtls_dev_k8s_woonyong_org"
WAF_RULE_DESCRIPTION = "Require a valid mTLS client certificate for dev-k8s.woonyong.org"
IDENTIFIER_RE = re.compile(r"^[0-9a-f]{32}$")
TUNNEL_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
CSR_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
PRIVATE_KEY_MARKER = "PRIVATE KEY"


class ConfigurationError(ValueError):
    pass


class CloudflareAPIError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, errors: list[dict[str, Any]]) -> None:
        self.method = method
        self.path = path
        self.status = status
        self.errors = errors
        messages = "; ".join(str(item.get("message", "unknown error")) for item in errors)
        super().__init__(f"Cloudflare API {method} {path} failed ({status}): {messages}")


@dataclass(frozen=True)
class CSRRequest:
    name: str
    csr: str


@dataclass(frozen=True)
class WAFPlan:
    action: str
    ruleset_id: str | None = None
    rule_id: str | None = None


@dataclass(frozen=True)
class PreflightState:
    tunnel_config: dict[str, Any]
    dns_records: list[dict[str, Any]]
    hostname_associations: list[str]
    waf_ruleset: dict[str, Any] | None
    client_certificates: list[dict[str, Any]]


def normalize_pem(value: str, label: str) -> str:
    normalized = value.strip().replace("\r\n", "\n").replace("\r", "\n")
    begin = f"-----BEGIN {label}-----"
    end = f"-----END {label}-----"
    if not normalized.startswith(begin) or not normalized.endswith(end):
        raise ConfigurationError(f"PEM must contain exactly one {label} block")
    if PRIVATE_KEY_MARKER in normalized:
        raise ConfigurationError("private key material is forbidden")
    body = "".join(normalized[len(begin) : -len(end)].split())
    try:
        der = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConfigurationError(f"invalid base64 in {label} PEM") from exc
    _validate_signed_der(der, label)
    encoded = base64.b64encode(der).decode("ascii")
    lines = "\n".join(encoded[index : index + 64] for index in range(0, len(encoded), 64))
    return f"{begin}\n{lines}\n{end}\n"


def _read_der_tlv(data: bytes, offset: int) -> tuple[int, bytes, int]:
    if offset + 2 > len(data):
        raise ConfigurationError("truncated DER value")
    tag = data[offset]
    first_length = data[offset + 1]
    cursor = offset + 2
    if first_length & 0x80:
        length_octets = first_length & 0x7F
        if length_octets == 0 or length_octets > 4 or cursor + length_octets > len(data):
            raise ConfigurationError("invalid DER length")
        if data[cursor] == 0:
            raise ConfigurationError("non-minimal DER length")
        length = int.from_bytes(data[cursor : cursor + length_octets], "big")
        cursor += length_octets
    else:
        length = first_length
    end = cursor + length
    if end > len(data):
        raise ConfigurationError("truncated DER payload")
    return tag, data[cursor:end], end


def _der_child_tags(content: bytes) -> list[tuple[int, bytes]]:
    children: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(content):
        tag, value, offset = _read_der_tlv(content, offset)
        children.append((tag, value))
    return children


def _validate_signed_der(der: bytes, label: str) -> None:
    try:
        top_tag, top_content, end = _read_der_tlv(der, 0)
        children = _der_child_tags(top_content)
    except ConfigurationError as exc:
        raise ConfigurationError(f"invalid DER payload in {label} PEM") from exc
    if end != len(der) or top_tag != 0x30 or [tag for tag, _ in children] != [0x30, 0x30, 0x03]:
        raise ConfigurationError(f"invalid signed-object structure in {label} PEM")
    if label == "CERTIFICATE REQUEST":
        request_info_tags = [tag for tag, _ in _der_child_tags(children[0][1])]
        if request_info_tags != [0x02, 0x30, 0x30, 0xA0]:
            raise ConfigurationError("invalid PKCS#10 structure in CERTIFICATE REQUEST PEM")


def decode_csr_batch(encoded: str) -> list[CSRRequest]:
    if not encoded or any(character.isspace() for character in encoded):
        raise ConfigurationError("CSR input must be non-empty, single-line base64")
    try:
        decoded = base64.b64decode(encoded, validate=True)
        payload = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("CSR input must be base64-encoded UTF-8 JSON") from exc
    if not isinstance(payload, list) or len(payload) != 5:
        raise ConfigurationError("CSR JSON must be an array containing exactly five entries")

    requests: list[CSRRequest] = []
    names: set[str] = set()
    csrs: set[str] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or set(item) != {"name", "csr"}:
            raise ConfigurationError(f"CSR entry {index + 1} must contain only name and csr")
        name = item["name"]
        csr = item["csr"]
        if not isinstance(name, str) or not CSR_NAME_RE.fullmatch(name):
            raise ConfigurationError(f"CSR entry {index + 1} has an invalid name")
        if name in names:
            raise ConfigurationError(f"duplicate CSR name: {name}")
        if not isinstance(csr, str):
            raise ConfigurationError(f"CSR entry {index + 1} csr must be a string")
        normalized_csr = normalize_pem(csr, "CERTIFICATE REQUEST")
        if normalized_csr in csrs:
            raise ConfigurationError(f"duplicate CSR material in entry {index + 1}")
        names.add(name)
        csrs.add(normalized_csr)
        requests.append(CSRRequest(name=name, csr=normalized_csr))
    return requests


def validate_origin_service(service: str) -> str:
    parsed = urllib.parse.urlsplit(service)
    if parsed.scheme not in {"http", "https", "tcp", "ssh", "rdp", "smb"}:
        raise ConfigurationError("origin service uses an unsupported cloudflared protocol")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(
            "origin service must be a host URL without credentials/query/fragment"
        )
    return service


def merge_tunnel_ingress(
    config: dict[str, Any], hostname: str, service: str
) -> tuple[dict[str, Any], str]:
    desired = {"hostname": hostname, "service": validate_origin_service(service)}
    merged = copy.deepcopy(config)
    ingress = merged.get("ingress")
    if not isinstance(ingress, list) or not ingress:
        raise ConfigurationError("remote tunnel configuration has no ingress rules")
    if any(not isinstance(rule, dict) for rule in ingress):
        raise ConfigurationError("remote tunnel ingress contains a non-object rule")

    full_host_indexes = [
        index
        for index, rule in enumerate(ingress)
        if rule.get("hostname") == hostname and not rule.get("path")
    ]
    if len(full_host_indexes) > 1:
        raise ConfigurationError(f"remote tunnel has duplicate full-host ingress for {hostname}")
    if full_host_indexes:
        index = full_host_indexes[0]
        if ingress[index].get("service") == service:
            return merged, "unchanged"
        ingress[index] = {**ingress[index], **desired}
        return merged, "update"

    fallback_indexes = [
        index
        for index, rule in enumerate(ingress)
        if not rule.get("hostname") and not rule.get("path")
    ]
    if not fallback_indexes:
        raise ConfigurationError("remote tunnel ingress has no catch-all rule")
    ingress.insert(fallback_indexes[0], desired)
    return merged, "create"


def desired_dns_record(tunnel_id: str) -> dict[str, Any]:
    return {
        "type": "CNAME",
        "name": HOSTNAME,
        "content": f"{tunnel_id}.cfargotunnel.com",
        "ttl": 1,
        "proxied": True,
    }


def plan_dns_record(
    records: list[dict[str, Any]], desired: dict[str, Any]
) -> tuple[str, str | None]:
    exact = [record for record in records if str(record.get("name", "")).rstrip(".") == HOSTNAME]
    if not exact:
        return "create", None
    if len(exact) != 1 or exact[0].get("type") != "CNAME":
        raise ConfigurationError(f"{HOSTNAME} has a conflicting or duplicate DNS record")
    current = exact[0]
    equivalent = (
        str(current.get("content", "")).rstrip(".") == str(desired["content"]).rstrip(".")
        and current.get("ttl") == desired["ttl"]
        and current.get("proxied") is True
    )
    return ("unchanged" if equivalent else "update"), str(current.get("id") or "")


def merge_hostname_associations(hostnames: list[str]) -> tuple[list[str], str]:
    normalized = [hostname.rstrip(".").lower() for hostname in hostnames]
    if HOSTNAME in normalized:
        return list(hostnames), "unchanged"
    return [*hostnames, HOSTNAME], "update"


def desired_waf_rule() -> dict[str, Any]:
    return {
        "action": "block",
        "expression": f'(http.host eq "{HOSTNAME}" and not cf.tls_client_auth.cert_verified)',
        "description": WAF_RULE_DESCRIPTION,
        "enabled": True,
        "ref": WAF_RULE_REF,
    }


def plan_waf_rule(ruleset: dict[str, Any] | None) -> WAFPlan:
    if ruleset is None:
        return WAFPlan(action="create_ruleset")
    ruleset_id = str(ruleset.get("id") or "")
    if not ruleset_id:
        raise ConfigurationError("WAF entrypoint ruleset is missing its id")
    rules = ruleset.get("rules")
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise ConfigurationError("WAF entrypoint ruleset has invalid rules")
    matches = [rule for rule in rules if rule.get("ref") == WAF_RULE_REF]
    if len(matches) > 1:
        raise ConfigurationError(f"WAF ruleset has duplicate ref {WAF_RULE_REF}")
    if not matches:
        return WAFPlan(action="create_rule", ruleset_id=ruleset_id)
    current = matches[0]
    desired = desired_waf_rule()
    if rules.index(current) == 0 and all(
        current.get(key) == value for key, value in desired.items()
    ):
        return WAFPlan(action="unchanged", ruleset_id=ruleset_id, rule_id=current.get("id"))
    rule_id = str(current.get("id") or "")
    if not rule_id:
        raise ConfigurationError("managed WAF rule is missing its id")
    return WAFPlan(action="update_rule", ruleset_id=ruleset_id, rule_id=rule_id)


def find_reusable_certificate(
    certificates: list[dict[str, Any]], request: CSRRequest, validity_days: int
) -> dict[str, Any] | None:
    matches = []
    for certificate in certificates:
        try:
            normalized = normalize_pem(str(certificate.get("csr", "")), "CERTIFICATE REQUEST")
        except ConfigurationError:
            continue
        if (
            normalized == request.csr
            and certificate.get("status") == "active"
            and certificate.get("validity_days") == validity_days
        ):
            matches.append(certificate)
    if not matches:
        return None
    return max(matches, key=lambda item: str(item.get("issued_on", "")))


class CloudflareAPI:
    def __init__(self, token: str, timeout: int = 20, attempts: int = 3) -> None:
        if not token:
            raise ConfigurationError("Cloudflare API token is required")
        self._token = token
        self._timeout = timeout
        self._attempts = attempts

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{API_BASE}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "kubeheal-cloudflare-mtls/1.0",
            },
        )
        for attempt in range(1, self._attempts + 1):
            try:
                with urllib.request.urlopen(
                    request, timeout=self._timeout, context=ssl.create_default_context()
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    status = response.getcode()
            except urllib.error.HTTPError as exc:
                payload = _decode_error_payload(exc.read())
                status = exc.code
                if status == 429 or status >= 500:
                    if attempt < self._attempts:
                        time.sleep(2 ** (attempt - 1))
                        continue
                raise CloudflareAPIError(method, path, status, _api_errors(payload)) from None
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < self._attempts:
                    time.sleep(2 ** (attempt - 1))
                    continue
                raise RuntimeError(f"Cloudflare API {method} {path} transport failure") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Cloudflare API {method} {path} returned invalid JSON") from exc
            if not isinstance(payload, dict) or payload.get("success") is not True:
                raise CloudflareAPIError(method, path, status, _api_errors(payload))
            return payload.get("result")
        raise AssertionError("unreachable")

    def list_all(
        self,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        pagination: str = "page",
    ) -> list[dict[str, Any]]:
        if pagination not in {"page", "cursor"}:
            raise ValueError("pagination must be page or cursor")
        items: list[dict[str, Any]] = []
        page = 1
        cursor = ""
        while True:
            page_query = {**(query or {}), "per_page": 50}
            if pagination == "page":
                page_query["page"] = page
            elif cursor:
                page_query["cursor"] = cursor
            url = f"{API_BASE}{path}?{urllib.parse.urlencode(page_query)}"
            request = urllib.request.Request(
                url,
                method="GET",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                    "User-Agent": "kubeheal-cloudflare-mtls/1.0",
                },
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self._timeout, context=ssl.create_default_context()
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    status = response.getcode()
            except urllib.error.HTTPError as exc:
                payload = _decode_error_payload(exc.read())
                raise CloudflareAPIError("GET", path, exc.code, _api_errors(payload)) from None
            if not isinstance(payload, dict) or payload.get("success") is not True:
                raise CloudflareAPIError("GET", path, status, _api_errors(payload))
            result = payload.get("result")
            if not isinstance(result, list):
                raise RuntimeError(f"Cloudflare API GET {path} returned a non-list result")
            items.extend(item for item in result if isinstance(item, dict))
            info = payload.get("result_info") or {}
            if pagination == "cursor":
                next_cursor = str((info.get("cursors") or {}).get("after") or "")
                if not next_cursor:
                    return items
                cursor = next_cursor
                continue
            total_pages = int(info.get("total_pages") or 0)
            if (total_pages and page >= total_pages) or (not total_pages and len(result) < 50):
                return items
            page += 1


def _decode_error_payload(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"errors": [{"message": "non-JSON error response"}]}
    return payload if isinstance(payload, dict) else {"errors": [{"message": "invalid response"}]}


def _api_errors(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("errors"), list):
        return [item for item in payload["errors"] if isinstance(item, dict)] or [
            {"message": "unknown error"}
        ]
    return [{"message": "unknown error"}]


def validate_identifiers(account_id: str, zone_id: str, tunnel_id: str) -> None:
    if not IDENTIFIER_RE.fullmatch(account_id):
        raise ConfigurationError("account id must be 32 lowercase hexadecimal characters")
    if zone_id and not IDENTIFIER_RE.fullmatch(zone_id):
        raise ConfigurationError("zone id must be 32 lowercase hexadecimal characters")
    if not TUNNEL_ID_RE.fullmatch(tunnel_id):
        raise ConfigurationError("tunnel id must be a lowercase UUID")


def resolve_zone_id(api: CloudflareAPI, account_id: str, configured_zone_id: str) -> str:
    """zone ID가 없으면 account와 zone 이름으로 단 하나의 활성 zone을 찾는다."""
    if configured_zone_id:
        return configured_zone_id
    zones = api.list_all(
        "/zones",
        query={"name": ZONE_NAME, "account.id": account_id, "status": "active"},
    )
    matches = [zone for zone in zones if zone.get("name") == ZONE_NAME]
    if len(matches) != 1:
        raise ConfigurationError(f"expected exactly one active {ZONE_NAME} zone")
    zone_id = str(matches[0].get("id") or "")
    if not IDENTIFIER_RE.fullmatch(zone_id):
        raise ConfigurationError("discovered zone has an invalid id")
    return zone_id


def preflight(api: CloudflareAPI, account_id: str, zone_id: str, tunnel_id: str) -> PreflightState:
    # 계정 API 토큰은 사용자 토큰과 검증 경로가 다르다.
    token = api.request("GET", f"/accounts/{account_id}/tokens/verify")
    if not isinstance(token, dict) or token.get("status") != "active":
        raise ConfigurationError("Cloudflare API token is not active")

    zone = api.request("GET", f"/zones/{zone_id}")
    if not isinstance(zone, dict):
        raise ConfigurationError("Cloudflare zone preflight returned an invalid result")
    if zone.get("name") != ZONE_NAME or (zone.get("account") or {}).get("id") != account_id:
        raise ConfigurationError(f"zone id must identify {ZONE_NAME} in the configured account")
    if zone.get("status") != "active":
        raise ConfigurationError(f"Cloudflare zone is not active: {zone.get('status', 'unknown')}")

    tunnel = api.request("GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}")
    if not isinstance(tunnel, dict):
        raise ConfigurationError("Cloudflare tunnel preflight returned an invalid result")
    if tunnel.get("account_tag") != account_id or tunnel.get("deleted_at"):
        raise ConfigurationError("tunnel does not belong to the account or is deleted")
    if tunnel.get("config_src") != "cloudflare":
        raise ConfigurationError("tunnel must be remotely managed (config_src=cloudflare)")

    configuration = api.request(
        "GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
    )
    if not isinstance(configuration, dict) or not isinstance(configuration.get("config"), dict):
        raise ConfigurationError("remote tunnel configuration is missing")
    dns_records = api.list_all(
        f"/zones/{zone_id}/dns_records", query={"name": HOSTNAME, "match": "all"}
    )
    associations = api.request(
        "GET", f"/zones/{zone_id}/certificate_authorities/hostname_associations"
    )
    if not isinstance(associations, dict):
        raise ConfigurationError("mTLS hostname association preflight returned an invalid result")
    raw_hostnames = associations.get("hostnames")
    if raw_hostnames is None:
        raw_hostnames = []
    if not isinstance(raw_hostnames, list):
        raise ConfigurationError("mTLS hostname association preflight returned an invalid result")

    rulesets = api.list_all(f"/zones/{zone_id}/rulesets", pagination="cursor")
    entrypoints = [
        item for item in rulesets if item.get("phase") == WAF_PHASE and item.get("kind") == "zone"
    ]
    if len(entrypoints) > 1:
        raise ConfigurationError("zone has multiple WAF custom phase entrypoint rulesets")
    waf_ruleset = None
    if entrypoints:
        ruleset_id = str(entrypoints[0].get("id") or "")
        result = api.request("GET", f"/zones/{zone_id}/rulesets/{ruleset_id}")
        if not isinstance(result, dict):
            raise ConfigurationError("WAF entrypoint ruleset returned an invalid result")
        waf_ruleset = result
    certificates = api.list_all(f"/zones/{zone_id}/client_certificates")
    return PreflightState(
        tunnel_config=configuration["config"],
        dns_records=dns_records,
        hostname_associations=list(raw_hostnames),
        waf_ruleset=waf_ruleset,
        client_certificates=certificates,
    )


def apply_configuration(
    api: CloudflareAPI,
    state: PreflightState,
    *,
    account_id: str,
    zone_id: str,
    tunnel_id: str,
    origin_service: str,
    dry_run: bool,
) -> list[str]:
    tunnel_config, tunnel_action = merge_tunnel_ingress(
        state.tunnel_config, HOSTNAME, origin_service
    )
    dns = desired_dns_record(tunnel_id)
    dns_action, record_id = plan_dns_record(state.dns_records, dns)
    hostnames, association_action = merge_hostname_associations(state.hostname_associations)
    waf_plan = plan_waf_rule(state.waf_ruleset)
    if dns_action == "update" and not record_id:
        raise ConfigurationError("existing DNS record is missing its id")
    actions = [
        f"tunnel ingress: {tunnel_action}",
        f"DNS CNAME: {dns_action}",
        f"mTLS hostname association: {association_action}",
        f"mTLS WAF rule: {waf_plan.action}",
    ]
    if not dry_run:
        if tunnel_action != "unchanged":
            api.request(
                "PUT",
                f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
                body={"config": tunnel_config},
            )
        if dns_action == "create":
            api.request("POST", f"/zones/{zone_id}/dns_records", body=dns)
        elif dns_action == "update":
            api.request("PATCH", f"/zones/{zone_id}/dns_records/{record_id}", body=dns)
        if association_action != "unchanged":
            api.request(
                "PUT",
                f"/zones/{zone_id}/certificate_authorities/hostname_associations",
                body={"hostnames": hostnames},
            )
        rule = desired_waf_rule()
        positioned_rule = {**rule, "position": {"index": 1}}
        if waf_plan.action == "create_ruleset":
            api.request(
                "POST",
                f"/zones/{zone_id}/rulesets",
                body={
                    "name": "Zone custom firewall rules",
                    "description": "Zone-level custom WAF rules managed through the Rulesets API",
                    "kind": "zone",
                    "phase": WAF_PHASE,
                    "rules": [rule],
                },
            )
        elif waf_plan.action == "create_rule":
            api.request(
                "POST",
                f"/zones/{zone_id}/rulesets/{waf_plan.ruleset_id}/rules",
                body=positioned_rule,
            )
        elif waf_plan.action == "update_rule":
            update_rule = rule
            current_rules = (
                state.waf_ruleset.get("rules", []) if state.waf_ruleset is not None else []
            )
            current_index = next(
                (
                    index
                    for index, current_rule in enumerate(current_rules)
                    if current_rule.get("id") == waf_plan.rule_id
                ),
                -1,
            )
            if current_index > 0:
                update_rule = positioned_rule
            api.request(
                "PATCH",
                f"/zones/{zone_id}/rulesets/{waf_plan.ruleset_id}/rules/{waf_plan.rule_id}",
                body=update_rule,
            )
    return actions


def issue_certificates(
    api: CloudflareAPI,
    requests: list[CSRRequest],
    existing: list[dict[str, Any]],
    *,
    zone_id: str,
    validity_days: int,
    dry_run: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    certificates: list[dict[str, Any]] = []
    actions: list[str] = []
    for request in requests:
        certificate = find_reusable_certificate(existing, request, validity_days)
        action = "reuse" if certificate else "issue"
        actions.append(f"client certificate {request.name}: {action}")
        if dry_run:
            continue
        if certificate is None:
            result = api.request(
                "POST",
                f"/zones/{zone_id}/client_certificates",
                body={"csr": request.csr.rstrip("\n"), "validity_days": validity_days},
            )
            if not isinstance(result, dict):
                raise RuntimeError("client certificate issuance returned an invalid result")
            certificate = result
        elif not certificate.get("certificate"):
            certificate_id = str(certificate.get("id") or "")
            if not certificate_id:
                raise RuntimeError("reusable client certificate is missing its id")
            result = api.request("GET", f"/zones/{zone_id}/client_certificates/{certificate_id}")
            if not isinstance(result, dict):
                raise RuntimeError("client certificate details returned an invalid result")
            certificate = result
        certificates.append({"name": request.name, **certificate})
    return certificates, actions


def write_certificate_artifact(certificates: list[dict[str, Any]], output_dir: Path) -> None:
    if len(certificates) != 5:
        raise RuntimeError("exactly five signed certificates are required for the artifact")
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {"hostname": HOSTNAME, "certificates": []}
    for item in certificates:
        name = str(item["name"])
        certificate = normalize_pem(str(item.get("certificate", "")), "CERTIFICATE")
        filename = f"{name}.pem"
        path = output_dir / filename
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as handle:
            handle.write(certificate)
        manifest["certificates"].append(
            {
                "name": name,
                "file": filename,
                "id": item.get("id"),
                "status": item.get("status"),
                "issued_on": item.get("issued_on"),
                "expires_on": item.get("expires_on"),
                "fingerprint_sha256": item.get("fingerprint_sha256"),
                "serial_number": item.get("serial_number"),
            }
        )
    manifest_path = output_dir / "manifest.json"
    descriptor = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise argparse.ArgumentTypeError("must be true or false")
    return normalized == "true"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure Cloudflare Tunnel and mTLS for dev")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--zone-id", default="")
    parser.add_argument("--tunnel-id", required=True)
    parser.add_argument("--origin-service", required=True)
    parser.add_argument("--api-token-env", default="CLOUDFLARE_API_TOKEN")
    parser.add_argument("--csr-batch-env", default="CLOUDFLARE_CSR_BATCH_B64")
    parser.add_argument("--validity-days", type=int, default=365)
    parser.add_argument("--dry-run", type=parse_bool, default=True)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/cloudflare-client-certificates")
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_identifiers(args.account_id, args.zone_id, args.tunnel_id)
        if not 1 <= args.validity_days <= 3650:
            raise ConfigurationError("validity days must be between 1 and 3650")
        token = os.environ.get(args.api_token_env, "")
        requests = decode_csr_batch(os.environ.get(args.csr_batch_env, ""))
        api = CloudflareAPI(token)
        zone_id = resolve_zone_id(api, args.account_id, args.zone_id)
        print("Cloudflare preflight: token, zone, and remote tunnel reads")
        state = preflight(api, args.account_id, zone_id, args.tunnel_id)
        actions = apply_configuration(
            api,
            state,
            account_id=args.account_id,
            zone_id=zone_id,
            tunnel_id=args.tunnel_id,
            origin_service=args.origin_service,
            dry_run=args.dry_run,
        )
        certificates, certificate_actions = issue_certificates(
            api,
            requests,
            state.client_certificates,
            zone_id=zone_id,
            validity_days=args.validity_days,
            dry_run=args.dry_run,
        )
        actions.extend(certificate_actions)
        mode = "DRY RUN" if args.dry_run else "APPLY"
        print(f"Cloudflare mTLS {mode} plan:")
        for action in actions:
            print(f"- {action}")
        if not args.dry_run:
            write_certificate_artifact(certificates, args.output_dir)
            print(f"- signed public certificate artifact: {args.output_dir}")
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as summary:
                summary.write(f"## Cloudflare mTLS {mode}\n\n")
                for action in actions:
                    summary.write(f"- {action}\n")
        return 0
    except (ConfigurationError, CloudflareAPIError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
