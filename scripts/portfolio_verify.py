from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "resume.md",
    *(ROOT / "docs" / "portfolio").glob("*.md"),
    *(ROOT / "docs" / "evidence" / "network-cost").glob("*.md"),
)
LOCAL_LINK = re.compile(r"\[[^\]]+\]\((?!https?://|mailto:)([^)#]+)(?:#[^)]+)?\)")
AWS_ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
THIRD_PARTY_VOICE = ("우용님", "우용의", "민정이", "민정의")


def main() -> int:
    errors = [*broken_links(), *unsafe_public_text(), *evidence_drift()]
    if errors:
        print("portfolio verification failed")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"portfolio verification passed: {len(PUBLIC_DOCS)} documents")
    print("- local links: valid")
    print("- AWS account IDs and third-party voice: absent")
    print("- cost, topology, and benchmark claims: match raw evidence")
    return 0


def broken_links() -> list[str]:
    errors: list[str] = []
    for path in PUBLIC_DOCS:
        text = path.read_text(encoding="utf-8")
        for match in LOCAL_LINK.finditer(text):
            target = (path.parent / match.group(1)).resolve()
            if not target.exists():
                errors.append(f"broken link in {path.relative_to(ROOT)}: {match.group(1)}")
    return errors


def unsafe_public_text() -> list[str]:
    errors: list[str] = []
    for path in PUBLIC_DOCS:
        text = path.read_text(encoding="utf-8")
        if AWS_ACCOUNT_ID.search(text):
            errors.append(f"12-digit AWS account ID in {path.relative_to(ROOT)}")
        for token in THIRD_PARTY_VOICE:
            if token in text:
                errors.append(f"third-party voice '{token}' in {path.relative_to(ROOT)}")
    return errors


def evidence_drift() -> list[str]:
    errors: list[str] = []
    cost_rows = _csv_rows("docs/evidence/network-cost/raw/aws-regional-transfer-daily.csv")
    total_gb = sum(float(_first(row, "usage_gb", "gb")) for row in cost_rows)
    total_cost = sum(
        float(_first(row, "gross_usage_cost_usd", "unblended_cost_usd", "cost_usd", "cost"))
        for row in cost_rows
    )
    evidence_readme = (ROOT / "docs/evidence/network-cost/README.md").read_text(encoding="utf-8")
    if f"{total_gb:,.2f}GB" not in evidence_readme:
        errors.append(f"cost usage drift: expected {total_gb:,.2f}GB")
    if f"${total_cost:,.2f}" not in evidence_readme:
        errors.append(f"cost amount drift: expected ${total_cost:,.2f}")

    service_rows = _csv_rows(
        "docs/evidence/network-cost/raw/aws-regional-transfer-by-service.csv"
    )
    service_gb = sum(float(row["usage_gb"]) for row in service_rows)
    service_cost = sum(float(row["gross_usage_cost_usd"]) for row in service_rows)
    if abs(service_gb - total_gb) > 0.000001:
        errors.append(f"service usage does not reconcile: {service_gb} != {total_gb}")
    if abs(service_cost - total_cost) > 0.000001:
        errors.append(f"service cost does not reconcile: {service_cost} != {total_cost}")

    topology = _csv_rows("docs/evidence/network-cost/raw/git-topology-timeline.csv")[-1]
    for key, label in (("deployment_documents", "Deployment"), ("declared_replica_sum", "replica")):
        if topology[key] not in evidence_readme:
            errors.append(f"topology drift: {label}={topology[key]}")

    benchmark = json.loads(
        (ROOT / "docs/evidence/network-cost/raw/event-bus-benchmark.json").read_text(
            encoding="utf-8"
        )
    )
    benchmark_text = (ROOT / "docs/portfolio/13-architecture-cost-postmortem.md").read_text(
        encoding="utf-8"
    )
    for value in _benchmark_medians(benchmark):
        if f"{value:.3f}ms" not in benchmark_text:
            errors.append(f"benchmark drift: expected {value:.3f}ms")
    return errors


def _csv_rows(relative: str) -> list[dict[str, str]]:
    with (ROOT / relative).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _first(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    raise KeyError(f"none of {keys!r} found in {tuple(row)}")


def _benchmark_medians(value: object) -> tuple[float, float]:
    if not isinstance(value, dict):
        raise TypeError("benchmark root must be an object")
    inprocess = value["inprocess"]
    nats = value["nats_jetstream_loopback"]
    if not isinstance(inprocess, dict) or not isinstance(nats, dict):
        raise TypeError("benchmark transports must be objects")
    return float(inprocess["median_total_ms"]), float(nats["median_total_ms"])


if __name__ == "__main__":
    sys.exit(main())
