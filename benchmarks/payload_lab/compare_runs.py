from __future__ import annotations

import json
import os
import re
import statistics
from pathlib import Path
from typing import Any


UNITS = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}


def size_bytes(value: str) -> float:
    match = re.fullmatch(r"([0-9.]+)([A-Za-z]+)", value.strip())
    if match is None:
        return 0.0
    return float(match.group(1)) * UNITS.get(match.group(2), 1)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def docker_summary(path: Path) -> dict[str, Any]:
    values: dict[str, dict[str, list[float]]] = {}
    if not path.exists():
        return {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        name = str(row.get("Name", "unknown"))
        service = "benchmark" if "benchmark-run" in name else name.removeprefix("kyro-payload-benchmark-").removesuffix("-1")
        bucket = values.setdefault(service, {"cpu": [], "memory": [], "network_rx": [], "network_tx": []})
        bucket["cpu"].append(float(str(row.get("CPUPerc", "0%")).rstrip("%")))
        bucket["memory"].append(size_bytes(str(row.get("MemUsage", "0B")).split(" / ")[0]))
        network = str(row.get("NetIO", "0B / 0B")).split(" / ")
        bucket["network_rx"].append(size_bytes(network[0]))
        bucket["network_tx"].append(size_bytes(network[1]))
    return {
        service: {
            "samples": len(bucket["cpu"]),
            "cpu_percent_p50": statistics.median(bucket["cpu"]),
            "cpu_percent_p95": sorted(bucket["cpu"])[max(0, int(len(bucket["cpu"]) * 0.95) - 1)],
            "cpu_percent_max": max(bucket["cpu"]),
            "memory_bytes_p50": statistics.median(bucket["memory"]),
            "memory_bytes_max": max(bucket["memory"]),
            "network_rx_bytes_delta": max(0, bucket["network_rx"][-1] - bucket["network_rx"][0]),
            "network_tx_bytes_delta": max(0, bucket["network_tx"][-1] - bucket["network_tx"][0]),
        }
        for service, bucket in values.items()
        if bucket["cpu"]
    }


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * percentile_value
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def nested_value(value: dict[str, Any], path: str) -> float:
    current: Any = value
    for part in path.split("."):
        current = current[part]
    return float(current)


def aggregate_summary(path: Path) -> dict[str, Any]:
    cycles = load_json(path)
    fields = (
        "raw_provider_sum_bytes",
        "agent_body_bytes",
        "full_evidence_bytes",
        "safe_ai_input_bytes",
        "nats.inline_full_bytes",
        "nats.claim_check_bytes",
        "database.full_logical_bytes",
        "database.full_physical_bytes",
        "database.compact_logical_bytes",
        "database.compact_physical_bytes",
    )
    summary: dict[str, Any] = {}
    for field in fields:
        values = [nested_value(row["aggregate"], field) for row in cycles]
        summary[field] = {
            "p50": statistics.median(values),
            "p95": percentile(values, 0.95),
            "max": max(values),
        }
    return summary


def provider_stage_summary(path: Path) -> dict[str, Any]:
    rows = load_json(path)
    fields = (
        "wire_bytes",
        "decoded_bytes",
        "raw_json_bytes",
        "normalized_bytes",
        "agent_body_bytes",
        "original_items",
        "returned_items",
        "transform_peak_bytes",
    )
    summary: dict[str, Any] = {}
    for provider in sorted({str(row["provider"]) for row in rows}):
        selected = [row for row in rows if row["provider"] == provider]
        provider_fields: dict[str, Any] = {}
        for field in fields:
            values = [float(row[field]) for row in selected]
            provider_fields[field] = {
                "p50": statistics.median(values),
                "p95": percentile(values, 0.95),
                "max": max(values),
            }
        http_ms = [float(row["http_seconds"]) * 1000 for row in selected]
        provider_fields["source_http_ms"] = {
            "p50": statistics.median(http_ms),
            "p95": percentile(http_ms, 0.95),
            "max": max(http_ms),
        }
        summary[provider] = provider_fields
    return summary


def main() -> None:
    root = Path(os.environ.get("RESULTS_ROOT", ".ecc/benchmarks/payload-experiment"))
    baseline_id = os.environ["BASELINE_RUN"]
    stress_id = os.environ["STRESS_RUN"]
    baseline_root = root / "runs" / baseline_id
    stress_root = root / "runs" / stress_id
    baseline = load_json(baseline_root / "summary.json")
    stress = load_json(stress_root / "summary.json")
    providers: dict[str, Any] = {}
    for provider in baseline["providers"]:
        before = baseline["providers"][provider]
        after = stress["providers"][provider]
        providers[provider] = {
            "baseline": before,
            "stress": after,
            "transform_p95_ratio": after["transform_ms_p95"] / before["transform_ms_p95"] if before["transform_ms_p95"] else None,
            "agent_body_p95_ratio": after["agent_body_bytes_p95"] / before["agent_body_bytes_p95"] if before["agent_body_bytes_p95"] else None,
            "peak_memory_ratio": after["transform_peak_bytes_max"] / before["transform_peak_bytes_max"] if before["transform_peak_bytes_max"] else None,
        }
    baseline_aggregate = aggregate_summary(baseline_root / "cycles.json")
    stress_aggregate = aggregate_summary(stress_root / "cycles.json")
    aggregate_ratios = {
        field: stress_aggregate[field]["p95"] / baseline_aggregate[field]["p95"]
        if baseline_aggregate[field]["p95"]
        else None
        for field in baseline_aggregate
    }
    baseline_stages = provider_stage_summary(baseline_root / "results.json")
    stress_stages = provider_stage_summary(stress_root / "results.json")
    stage_ratios = {
        provider: {
            field: stress_stages[provider][field]["p95"] / baseline_stages[provider][field]["p95"]
            if baseline_stages[provider][field]["p95"]
            else None
            for field in baseline_stages[provider]
        }
        for provider in baseline_stages
    }
    comparison = {
        "schema_version": 1,
        "baseline_run": baseline_id,
        "stress_run": stress_id,
        "measurement_notes": {
            "baseline_wire_bytes": "actual HTTP bytes downloaded from each local source API",
            "stress_wire_bytes": "controlled amplified JSON size after the same live query; not observed network traffic",
            "docker_network_delta": "container cumulative NetIO last sample minus first sample; includes protocol overhead and Loki/Tempo fixture seeding; the harness does not POST the serialized body to Management",
        },
        "providers": providers,
        "provider_stages": {
            "baseline": baseline_stages,
            "stress": stress_stages,
            "p95_ratio": stage_ratios,
        },
        "aggregate": {
            "baseline": baseline_aggregate,
            "stress": stress_aggregate,
            "p95_ratio": aggregate_ratios,
        },
        "docker": {
            "baseline": docker_summary(baseline_root / "docker-stats.jsonl"),
            "stress": docker_summary(stress_root / "docker-stats.jsonl"),
        },
        "validity": {
            "baseline_cycles": baseline["cycles"],
            "stress_cycles": stress["cycles"],
            "baseline_contract_failures": baseline["total_contract_failures"],
            "stress_contract_failures": stress["total_contract_failures"],
            "baseline_truth_failures": baseline["total_truth_signal_failures"],
            "stress_truth_failures": stress["total_truth_signal_failures"],
        },
    }
    destination = root / f"comparison-{baseline_id}-vs-{stress_id}.json"
    destination.write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n")
    print(destination)


if __name__ == "__main__":
    main()
