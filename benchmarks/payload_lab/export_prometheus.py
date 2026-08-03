from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

QUERIES = {
    "transform_seconds": 'kyro_payload_transform_seconds{run_id="%s"}',
    "agent_body_bytes": 'kyro_payload_stage_bytes{run_id="%s",stage="agent_body_bytes"}',
    "transformation_stages": 'sum by (stage) (kyro_payload_stage_bytes{run_id="%s",stage=~"wire_bytes|decoded_bytes|normalized_bytes|agent_body_bytes"})',
    "truth_retention_ratio": 'kyro_payload_truth_retention_ratio{run_id="%s"}',
    "source_truth_ratio": 'kyro_payload_source_truth_ratio{run_id="%s"}',
    "http_contract_valid": 'kyro_payload_http_contract_valid{run_id="%s"}',
    "reconciliation_failures": 'kyro_payload_reconciliation_failures{run_id="%s"}',
}


def query_range(api_url: str, query: str, start: str, end: str, step: int) -> list[dict[str, Any]]:
    parameters = urllib.parse.urlencode({"query": query, "start": start, "end": end, "step": step})
    with urllib.request.urlopen(f"{api_url.rstrip('/')}/api/v1/query_range?{parameters}", timeout=30) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise RuntimeError(payload)
    return payload["data"]["result"]


def main() -> None:
    run_id = os.environ["RUN_ID"]
    results_root = Path(os.environ.get("RESULTS_ROOT", ".ecc/benchmarks/payload-experiment"))
    run_root = results_root / "runs" / run_id
    summary = json.loads((run_root / "summary.json").read_text())
    api_url = os.environ.get("PROMETHEUS_API_URL", "http://localhost:59090")
    exported = {
        "schema_version": 1,
        "run_id": run_id,
        "start": summary["started_at"],
        "end": summary["finished_at"],
        "step_seconds": 5,
        "note": "Prometheus scrapes the latest 30-second cycle gauge every 5 seconds; sample count is not cycle count.",
        "queries": {
            name: {
                "promql": template % run_id,
                "series": query_range(api_url, template % run_id, summary["started_at"], summary["finished_at"], 5),
            }
            for name, template in QUERIES.items()
        },
    }
    destination = run_root / "prometheus-range.json"
    destination.write_text(json.dumps(exported, ensure_ascii=False, indent=2) + "\n")
    print(destination)


if __name__ == "__main__":
    main()
