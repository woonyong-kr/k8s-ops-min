from __future__ import annotations

import asyncio
import csv
import json
import os
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from benchmarks.payload_lab import runner as lab
from domains.rca.events import ClusterEvidenceReceivedBody, compact_cluster_evidence_payload, evidence_payload_size


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * percentile_value
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def summarize(rows: list[dict[str, Any]], case: str, started: datetime, finished: datetime) -> dict[str, Any]:
    providers: dict[str, Any] = {}
    for provider in lab.PROVIDERS:
        selected = [row for row in rows if row["provider"] == provider]
        durations = [float(row["transform_seconds_median"]) for row in selected]
        bodies = [float(row["agent_body_bytes"]) for row in selected]
        peaks = [float(row["transform_peak_bytes"]) for row in selected]
        providers[provider] = {
            "cycles": len(selected),
            "transform_ms_p50": statistics.median(durations) * 1000 if durations else 0,
            "transform_ms_p95": percentile(durations, 0.95) * 1000,
            "transform_ms_max": max(durations, default=0) * 1000,
            "agent_body_bytes_p50": statistics.median(bodies) if bodies else 0,
            "agent_body_bytes_p95": percentile(bodies, 0.95),
            "agent_body_bytes_max": max(bodies, default=0),
            "transform_peak_bytes_max": max(peaks, default=0),
            "contract_failures": sum(not bool(row["contract_valid"]) for row in selected),
            "source_truth_missing": sum(float(row["source_truth_ratio"]) < 1 for row in selected),
            "truth_signal_failures": sum(bool(row["transform_truth_failure"]) for row in selected),
        }
    return {
        "run_id": os.environ["RUN_ID"],
        "case": case,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "elapsed_seconds": (finished - started).total_seconds(),
        "cycles": len(rows) // len(lab.PROVIDERS),
        "providers": providers,
        "total_contract_failures": sum(not bool(row["contract_valid"]) for row in rows),
        "total_source_truth_missing": sum(float(row["source_truth_ratio"]) < 1 for row in rows),
        "total_truth_signal_failures": sum(bool(row["transform_truth_failure"]) for row in rows),
    }


async def main() -> None:
    case = os.environ.get("SOAK_CASE", "baseline")
    duration_seconds = int(os.environ.get("SOAK_SECONDS", "1800"))
    interval_seconds = int(os.environ.get("SOAK_INTERVAL_SECONDS", "30"))
    run_id = os.environ["RUN_ID"]
    run_root = Path(os.environ["RESULTS_ROOT"]) / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).replace(microsecond=0)
    deadline = time.monotonic() + duration_seconds
    next_cycle = time.monotonic()
    all_rows: list[dict[str, Any]] = []
    cycle_summaries: list[dict[str, Any]] = []
    cycle = 0
    multipliers = {"kubernetes": 20, "prometheus": 10, "loki": 4, "tempo": 500}
    transforms = {
        "kubernetes": lab.normalize_kubernetes,
        "prometheus": lab.normalize_prometheus,
        "loki": lab.normalize_loki,
        "tempo": lab.normalize_tempo,
    }

    while time.monotonic() < deadline:
        cycle += 1
        os.environ["COLLECTION_CYCLE"] = str(cycle)
        cycle_started = datetime.now(UTC).replace(microsecond=0)
        raw_by_provider, http_by_provider = await lab.collect_actual_sources()
        normalized: dict[str, Any] = {}
        rows: list[dict[str, Any]] = []
        for provider in lab.PROVIDERS:
            raw = raw_by_provider[provider]
            http = http_by_provider[provider]
            if case == "stress":
                raw = lab.scale_raw(provider, raw, multipliers[provider])
                raw_size = len(lab.compact_json(raw))
                http = {
                    "wire_bytes": raw_size,
                    "decoded_bytes": raw_size,
                    "content_encoding": "controlled-amplification",
                    "http_seconds": 0.0,
                    "status_code": 200,
                }
            result, row = lab.measured_row(provider, case, raw, http, transforms[provider])
            row["cycle"] = cycle
            row["observed_at"] = cycle_started.isoformat()
            normalized[provider] = result
            rows.append(row)

        window_start = (cycle_started - timedelta(seconds=lab.WINDOW_SECONDS)).isoformat()
        body = ClusterEvidenceReceivedBody(
            workspace_id="payload-lab",
            cluster_id="payload-lab",
            agent_id="payload-lab-agent",
            source_id=f"soak-{case}",
            window_start=window_start,
            evidence_key=f"payload-lab:{run_id}:{cycle}",
            correlation_id=f"{run_id}:{cycle}",
            kubernetes=normalized["kubernetes"],
            metrics=normalized["prometheus"],
            logs=normalized["loki"],
            traces=normalized["tempo"],
            collection_status=lab.collection_status(),
            metadata={"run_id": run_id, "case": case, "cycle": cycle},
        )
        full = body.to_body()
        compact = compact_cluster_evidence_payload(body, f"{run_id}:{cycle}")
        failures = []
        if any(float(row["source_truth_ratio"]) < 1 for row in rows):
            failures.append("source_truth_missing")
        if any(bool(row["transform_truth_failure"]) for row in rows):
            failures.append("transformation_truth_loss")
        if any(not bool(row["contract_valid"]) for row in rows):
            failures.append("provider_contract_rejected")
        aggregate = {
            "raw_provider_sum_bytes": sum(row["decoded_bytes"] for row in rows),
            "agent_body_bytes": sum(row["agent_body_bytes"] for row in rows),
            "full_evidence_bytes": evidence_payload_size(full),
            "safe_ai_input_bytes": len(lab.compact_json({"summary": compact.get("summary", {}), "collection_status": body.collection_status})),
            "nats": await lab.nats_measure(full, compact),
            "database": lab.db_measure(full, compact),
            "reconciliation_failures": failures,
        }
        await lab.push_metrics(lab.prometheus_lines(rows, aggregate))
        all_rows.extend(rows)
        cycle_summaries.append({
            "cycle": cycle,
            "observed_at": cycle_started.isoformat(),
            "elapsed_seconds": (datetime.now(UTC) - cycle_started).total_seconds(),
            "aggregate": aggregate,
        })
        (run_root / "progress.json").write_text(json.dumps({"run_id": run_id, "case": case, "cycle": cycle, "last_observed_at": cycle_started.isoformat(), "failures": failures}, indent=2) + "\n")
        next_cycle += interval_seconds
        await asyncio.sleep(max(0, min(next_cycle, deadline) - time.monotonic()))

    finished = datetime.now(UTC).replace(microsecond=0)
    summary = summarize(all_rows, case, started, finished)
    (run_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    (run_root / "cycles.json").write_text(json.dumps(cycle_summaries, ensure_ascii=False, indent=2) + "\n")
    (run_root / "results.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=2) + "\n")
    fields = [key for key in all_rows[0] if key not in {"source_truth_checks", "truth_checks", "transform_seconds_samples"}]
    with (run_root / "results.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row[key] for key in fields} for row in all_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
