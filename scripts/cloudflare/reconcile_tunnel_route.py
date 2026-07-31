#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import Any

from scripts.cloudflare.configure_dev_mtls import (
    CloudflareAPI,
    CloudflareAPIError,
    ConfigurationError,
    merge_tunnel_ingress,
    resolve_zone_id,
    validate_identifiers,
    validate_origin_service,
)

HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]{2,63}\.?$"
)


@dataclass(frozen=True)
class RouteState:
    tunnel_config: dict[str, Any]
    dns_records: list[dict[str, Any]]


@dataclass(frozen=True)
class RoutePlan:
    tunnel_action: str
    tunnel_config: dict[str, Any]
    dns_action: str
    dns_record_id: str | None
    dns_record: dict[str, Any]


def validate_hostname(hostname: str) -> str:
    normalized = hostname.rstrip(".").lower()
    if not HOSTNAME_RE.fullmatch(normalized):
        raise ConfigurationError("hostname must be a fully-qualified DNS name")
    return normalized


def desired_dns_record(hostname: str, tunnel_id: str) -> dict[str, Any]:
    return {
        "type": "CNAME",
        "name": validate_hostname(hostname),
        "content": f"{tunnel_id}.cfargotunnel.com",
        "ttl": 1,
        "proxied": True,
    }


def plan_dns_record(
    records: list[dict[str, Any]], desired: dict[str, Any]
) -> tuple[str, str | None]:
    hostname = str(desired["name"])
    exact = [
        record for record in records if str(record.get("name", "")).rstrip(".").lower() == hostname
    ]
    if not exact:
        return "create", None
    if len(exact) != 1:
        raise ConfigurationError(f"{hostname} has duplicate DNS records")
    current = exact[0]
    record_id = str(current.get("id") or "")
    if not record_id:
        raise ConfigurationError(f"{hostname} DNS record is missing its id")
    equivalent = (
        current.get("type") == desired["type"]
        and str(current.get("content", "")).rstrip(".").lower()
        == str(desired["content"]).rstrip(".").lower()
        and current.get("ttl") == desired["ttl"]
        and current.get("proxied") is True
    )
    return ("unchanged" if equivalent else "update"), record_id


def read_route_state(
    api: CloudflareAPI,
    *,
    account_id: str,
    zone_id: str,
    tunnel_id: str,
    hostname: str,
) -> RouteState:
    tunnel = api.request("GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}")
    if not isinstance(tunnel, dict) or tunnel.get("deleted_at"):
        raise ConfigurationError("configured Cloudflare tunnel is missing or deleted")
    if tunnel.get("account_tag") != account_id:
        raise ConfigurationError("configured Cloudflare tunnel belongs to another account")
    if tunnel.get("config_src") != "cloudflare":
        raise ConfigurationError("configured Cloudflare tunnel must use remote configuration")

    configuration = api.request(
        "GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
    )
    if not isinstance(configuration, dict) or not isinstance(configuration.get("config"), dict):
        raise ConfigurationError("remote tunnel configuration is missing")
    dns_records = api.list_all(
        f"/zones/{zone_id}/dns_records",
        query={"name": hostname, "match": "all"},
    )
    return RouteState(
        tunnel_config=configuration["config"],
        dns_records=dns_records,
    )


def build_route_plan(
    state: RouteState,
    *,
    hostname: str,
    origin_service: str,
    tunnel_id: str,
) -> RoutePlan:
    normalized_hostname = validate_hostname(hostname)
    normalized_origin = validate_origin_service(origin_service)
    tunnel_config, tunnel_action = merge_tunnel_ingress(
        state.tunnel_config,
        normalized_hostname,
        normalized_origin,
    )
    dns_record = desired_dns_record(normalized_hostname, tunnel_id)
    dns_action, dns_record_id = plan_dns_record(state.dns_records, dns_record)
    return RoutePlan(
        tunnel_action=tunnel_action,
        tunnel_config=tunnel_config,
        dns_action=dns_action,
        dns_record_id=dns_record_id,
        dns_record=dns_record,
    )


def apply_route_plan(
    api: CloudflareAPI,
    plan: RoutePlan,
    *,
    account_id: str,
    zone_id: str,
    tunnel_id: str,
) -> None:
    if plan.tunnel_action != "unchanged":
        api.request(
            "PUT",
            f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
            body={"config": plan.tunnel_config},
        )
    if plan.dns_action == "create":
        api.request("POST", f"/zones/{zone_id}/dns_records", body=plan.dns_record)
    elif plan.dns_action == "update":
        if not plan.dns_record_id:
            raise ConfigurationError("DNS update plan is missing the record id")
        api.request(
            "PUT",
            f"/zones/{zone_id}/dns_records/{plan.dns_record_id}",
            body=plan.dns_record,
        )


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected a boolean value")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile one Cloudflare Tunnel hostname and its proxied DNS record."
    )
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--zone-id", default="")
    parser.add_argument("--tunnel-id", required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--origin-service", required=True)
    parser.add_argument("--dry-run", type=parse_bool, default=True)
    parser.add_argument("--api-token-env", default="CLOUDFLARE_API_TOKEN")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        validate_identifiers(args.account_id, args.zone_id, args.tunnel_id)
        hostname = validate_hostname(args.hostname)
        validate_origin_service(args.origin_service)
        api = CloudflareAPI(os.environ.get(args.api_token_env, ""))
        zone_id = resolve_zone_id(api, args.account_id, args.zone_id)
        state = read_route_state(
            api,
            account_id=args.account_id,
            zone_id=zone_id,
            tunnel_id=args.tunnel_id,
            hostname=hostname,
        )
        plan = build_route_plan(
            state,
            hostname=hostname,
            origin_service=args.origin_service,
            tunnel_id=args.tunnel_id,
        )
        print(f"Cloudflare route plan: tunnel_ingress={plan.tunnel_action} dns={plan.dns_action}")
        if args.dry_run:
            print("Dry-run enabled; Cloudflare was not changed.")
            return 0
        apply_route_plan(
            api,
            plan,
            account_id=args.account_id,
            zone_id=zone_id,
            tunnel_id=args.tunnel_id,
        )
        verified = build_route_plan(
            read_route_state(
                api,
                account_id=args.account_id,
                zone_id=zone_id,
                tunnel_id=args.tunnel_id,
                hostname=hostname,
            ),
            hostname=hostname,
            origin_service=args.origin_service,
            tunnel_id=args.tunnel_id,
        )
        if verified.tunnel_action != "unchanged" or verified.dns_action != "unchanged":
            raise RuntimeError("Cloudflare route postflight did not converge")
        print("Cloudflare route reconcile complete.")
        return 0
    except (CloudflareAPIError, ConfigurationError, RuntimeError) as exc:
        print(f"Cloudflare route reconcile failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
