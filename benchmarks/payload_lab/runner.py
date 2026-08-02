from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import statistics
import sys
import time
import tracemalloc
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx
import nats
import psycopg


ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = ROOT / "src/services/target/cluster-agent"
sys.path.insert(0, str(AGENT_DIR))
sys.path.insert(0, str(ROOT / "src"))

from domains.rca.events import (  # noqa: E402
    ClusterEvidenceReceivedBody,
    compact_cluster_evidence_payload,
    evidence_payload_size,
)
from packages.contracts.gateway.requests import (  # noqa: E402
    EvidenceJobResultRequest,
    MAX_EVIDENCE_PAYLOAD_BYTES,
)
from providers.collection_limits import payload_size_bytes  # noqa: E402
from providers.kubernetes_providers import (  # noqa: E402
    KubernetesSnapshotProvider,
)
from providers.loki_providers import LokiLogsProvider  # noqa: E402
from providers.prometheus_providers import PrometheusMetricsProvider  # noqa: E402
from providers.tempo_providers import TempoTracesProvider  # noqa: E402
from queries import (  # noqa: E402
    KubernetesSnapshotQuery,
    LokiLogQuery,
    OpenTelemetrySpanQuery,
    PrometheusInstantQuery,
)


PROVIDERS = ("kubernetes", "prometheus", "loki", "tempo")
WINDOW_SECONDS = 300
REPEATS = 5
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


def compact_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def benchmark_transform(fn: Callable[[], Any]) -> tuple[Any, float, int, list[float]]:
    fn()  # warm-up; excluded from the result
    durations: list[float] = []
    peaks: list[int] = []
    result: Any = None
    for _ in range(REPEATS):
        tracemalloc.start()
        started = time.perf_counter()
        result = fn()
        durations.append(time.perf_counter() - started)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)
    return result, statistics.median(durations), max(peaks), durations


async def get_json(client: httpx.AsyncClient, url: str, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    response = await client.get(url, **kwargs)
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    decoded = response.content
    wire = int(response.num_bytes_downloaded)
    return response.json(), {
        "wire_bytes": wire,
        "decoded_bytes": len(decoded),
        "content_encoding": response.headers.get("content-encoding", "identity"),
        "http_seconds": elapsed,
        "status_code": response.status_code,
    }


def kubernetes_query() -> KubernetesSnapshotQuery:
    return KubernetesSnapshotQuery(
        "payload_lab",
        "same-window payload experiment",
        "snapshot",
        None,
        "namespace",
        None,
    )


def normalize_kubernetes(raw: dict[str, Any]) -> dict[str, Any]:
    provider = KubernetesSnapshotProvider(cluster_id="payload-lab")
    return provider.build_response(provider.normalize_payload(raw, kubernetes_query()))


def normalize_prometheus(raw: dict[str, Any]) -> dict[str, Any]:
    provider = PrometheusMetricsProvider(os.environ["PROMETHEUS_URL"])
    query = PrometheusInstantQuery("payload_metric", "deterministic fixture", "kyro_payload_metric")
    results = provider.empty_results()
    provider.append_result(results, query, raw)
    return provider.build_response(results)


def normalize_loki(raw: dict[str, Any]) -> list[dict[str, Any]]:
    provider = LokiLogsProvider(os.environ["LOKI_URL"])
    query = LokiLogQuery("payload_logs", "error fixture", '{namespace="payload-bench"}', WINDOW_SECONDS)
    results = provider.empty_results()
    provider.append_result(results, query, raw)
    return provider.build_response(results)


def normalize_tempo(raw: dict[str, Any]) -> dict[str, Any]:
    provider = TempoTracesProvider(os.environ["TEMPO_URL"])
    query = OpenTelemetrySpanQuery("payload_traces", "error fixture", "{ status = error }", WINDOW_SECONDS)
    results = provider.empty_results()
    provider.append_result(results, query, raw)
    return provider.build_response(results)


def provider_truth(provider: str, normalized: Any) -> dict[str, bool]:
    encoded = compact_json(normalized).decode(errors="replace")
    if provider == "kubernetes":
        return {
            "failed_scheduling": "FailedScheduling" in encoded,
            "oom_signal": "OOM" in encoded,
        }
    if provider == "prometheus":
        return {"metric_99": '99.0' in encoded or ':99' in encoded or '[99' in encoded}
    if provider == "loki":
        return {
            "error": "error" in encoded.lower(),
            "dependency_timeout": "dependency_timeout" in encoded,
            "trace_id": TRACE_ID in encoded,
            "secret_redacted": "super-secret" not in encoded,
        }
    return {"error_trace": TRACE_ID in encoded or "STATUS_CODE_ERROR" in encoded}


def collection_status() -> dict[str, Any]:
    return {
        provider: {"status": "success", "reason_code": "collected", "partial": False}
        for provider in PROVIDERS
    }


async def seed_loki(client: httpx.AsyncClient, now_ns: int) -> None:
    values = []
    for index in range(160):
        severity = "ERROR" if index % 7 == 0 else "INFO"
        message = (
            f"level={severity} dependency timeout trace_id={TRACE_ID} "
            f"token=super-secret request={index} payload={'x' * (64 + index % 256)}"
        )
        values.append([str(now_ns - index * 10_000_000), message])
    response = await client.post(
        f"{os.environ['LOKI_URL']}/loki/api/v1/push",
        json={"streams": [{"stream": {"namespace": "payload-bench", "app": "payload-lab"}, "values": values}]},
    )
    response.raise_for_status()


async def seed_tempo(client: httpx.AsyncClient, now_ns: int) -> None:
    start = now_ns - 2_000_000_000
    body = {
        "resourceSpans": [{
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "payload-lab"}}]},
            "scopeSpans": [{"scope": {"name": "payload-lab"}, "spans": [{
                "traceId": TRACE_ID,
                "spanId": "00f067aa0ba902b7",
                "name": "dependency timeout",
                "kind": 2,
                "startTimeUnixNano": str(start),
                "endTimeUnixNano": str(now_ns),
                "status": {"code": 2, "message": "dependency timeout"},
                "attributes": [{"key": "http.status_code", "value": {"intValue": "504"}}],
            }]}],
        }]
    }
    response = await client.post(os.environ["TEMPO_OTLP_URL"], json=body)
    response.raise_for_status()


async def wait_ready(client: httpx.AsyncClient, url: str, attempts: int = 45) -> None:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            response = await client.get(url)
            if response.status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
        await asyncio.sleep(2)
    raise RuntimeError(f"service not ready: {url}: {last}")


async def collect_actual_sources() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    async with httpx.AsyncClient(timeout=30) as client:
        await wait_ready(client, f"{os.environ['PROMETHEUS_URL']}/-/ready")
        await wait_ready(client, f"{os.environ['LOKI_URL']}/ready")
        await wait_ready(client, f"{os.environ['TEMPO_URL']}/ready")
        now_ns = time.time_ns()
        await seed_loki(client, now_ns)
        await seed_tempo(client, now_ns)
        await asyncio.sleep(6)

        prometheus, prom_http = await get_json(
            client,
            f"{os.environ['PROMETHEUS_URL']}/api/v1/query",
            params={"query": "kyro_payload_metric"},
        )
        loki, loki_http = await get_json(
            client,
            f"{os.environ['LOKI_URL']}/loki/api/v1/query_range",
            params={
                "query": '{namespace="payload-bench"}',
                "limit": 1000,
                "start": str(now_ns - WINDOW_SECONDS * 1_000_000_000),
                "end": str(now_ns + 1_000_000_000),
            },
        )
        tempo, tempo_http = await get_json(
            client,
            f"{os.environ['TEMPO_URL']}/api/search",
            params={"q": "{ status = error }", "limit": 100, "start": int(time.time()) - WINDOW_SECONDS, "end": int(time.time()) + 1},
        )
    fixture = Path(os.environ["KUBERNETES_FIXTURE"])
    kubernetes = json.loads(fixture.read_text())
    k8_bytes = compact_json(kubernetes)
    return (
        {"kubernetes": kubernetes, "prometheus": prometheus, "loki": loki, "tempo": tempo},
        {
            "kubernetes": {"wire_bytes": len(k8_bytes), "decoded_bytes": len(k8_bytes), "content_encoding": "fixture-from-kubernetes-api", "http_seconds": 0.0, "status_code": 200},
            "prometheus": prom_http,
            "loki": loki_http,
            "tempo": tempo_http,
        },
    )


def validate_agent_contract(provider: str, normalized: Any) -> tuple[bool, str]:
    try:
        EvidenceJobResultRequest(
            agent_id="payload-lab-agent",
            lease_id=f"{provider}-lease",
            status="completed",
            result={"provider": provider, "evidence": normalized},
        )
        return True, "accepted"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


def db_measure(full: dict[str, Any], compact: dict[str, Any]) -> dict[str, int]:
    with psycopg.connect(os.environ["POSTGRES_DSN"], autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS payload_lab (run_id text PRIMARY KEY, full_payload jsonb, compact_payload jsonb)")
            cur.execute(
                "INSERT INTO payload_lab(run_id, full_payload, compact_payload) VALUES (%s, %s, %s) ON CONFLICT (run_id) DO UPDATE SET full_payload=EXCLUDED.full_payload, compact_payload=EXCLUDED.compact_payload",
                (os.environ["RUN_ID"], json.dumps(full), json.dumps(compact)),
            )
            cur.execute(
                "SELECT octet_length(full_payload::text), pg_column_size(full_payload), octet_length(compact_payload::text), pg_column_size(compact_payload) FROM payload_lab WHERE run_id=%s",
                (os.environ["RUN_ID"],),
            )
            logical_full, physical_full, logical_compact, physical_compact = cur.fetchone()
    return {
        "full_logical_bytes": logical_full,
        "full_physical_bytes": physical_full,
        "compact_logical_bytes": logical_compact,
        "compact_physical_bytes": physical_compact,
    }


async def nats_measure(full: dict[str, Any], compact: dict[str, Any]) -> dict[str, int]:
    connection = await nats.connect(os.environ["NATS_URL"])
    full_bytes = compact_json(full)
    compact_bytes = compact_json(compact)
    await connection.publish(f"payload.lab.{os.environ['RUN_ID']}.compact", compact_bytes)
    await connection.flush()
    await connection.close()
    return {"inline_full_bytes": len(full_bytes), "claim_check_bytes": len(compact_bytes)}


def prometheus_lines(rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> str:
    run_id = os.environ["RUN_ID"]
    labels = lambda **values: ",".join(f'{key}="{str(value)}"' for key, value in {"run_id": run_id, **values}.items())
    lines = ["# TYPE kyro_payload_stage_bytes gauge"]
    for row in rows:
        provider = row["provider"]
        for stage in ("wire_bytes", "decoded_bytes", "normalized_bytes", "agent_body_bytes"):
            lines.append(f'kyro_payload_stage_bytes{{{labels(provider=provider, stage=stage)}}} {row[stage]}')
        lines.append(f'kyro_payload_truth_retention_ratio{{{labels(provider=provider)}}} {row["truth_retention_ratio"]}')
        lines.append(f'kyro_payload_http_contract_valid{{{labels(provider=provider)}}} {int(row["contract_valid"])}')
        lines.append(f'kyro_payload_transform_seconds{{{labels(provider=provider)}}} {row["transform_seconds_median"]}')
        lines.append(f'kyro_payload_items{{{labels(provider=provider, stage="original")}}} {row["original_items"]}')
        lines.append(f'kyro_payload_items{{{labels(provider=provider, stage="returned")}}} {row["returned_items"]}')
    lines.append(f'kyro_payload_reconciliation_failures{{{labels()}}} {len(aggregate["reconciliation_failures"])}')
    for name, value in aggregate["nats"].items():
        lines.append(f'kyro_payload_nats_bytes{{{labels(stage=name)}}} {value}')
    for name, value in aggregate["database"].items():
        lines.append(f'kyro_payload_db_bytes{{{labels(stage=name)}}} {value}')
    for name in ("raw_provider_sum_bytes", "agent_body_bytes", "full_evidence_bytes"):
        lines.append(f'kyro_payload_aggregate_bytes{{{labels(stage=name)}}} {aggregate[name]}')
    lines.append(f'kyro_payload_prompt_bytes{{{labels()}}} {aggregate["safe_ai_input_bytes"]}')
    lines.append(f'kyro_payload_prompt_tokens_estimate{{{labels()}}} {aggregate["safe_ai_input_bytes"] / 4:.2f}')
    return "\n".join(lines) + "\n"


def count_raw(provider: str, raw: Any) -> int:
    if provider == "kubernetes":
        return sum(len(value.get("items", [])) for value in raw.values() if isinstance(value, dict) and isinstance(value.get("items"), list))
    if provider == "prometheus":
        return len(raw.get("data", {}).get("result", []))
    if provider == "loki":
        return sum(len(stream.get("values", [])) for stream in raw.get("data", {}).get("result", []))
    return len(raw.get("traces", []))


def count_normalized(provider: str, normalized: Any) -> int:
    if provider == "kubernetes":
        return sum(len(value) for value in normalized.values() if isinstance(value, list))
    if provider == "prometheus":
        return sum(len(value.get("samples", [])) for value in normalized.get("results", {}).values())
    if provider == "loki":
        return sum(sum(len(stream.get("values", [])) for stream in result.get("streams", [])) for result in normalized)
    return sum(len(value.get("traces", [])) for value in normalized.get("results", {}).values())


async def push_metrics(metrics: str) -> None:
    url = f"{os.environ['PUSHGATEWAY_URL']}/metrics/job/kyro_payload/run_id/{urllib.parse.quote(os.environ['RUN_ID'])}"
    request = urllib.request.Request(url, data=metrics.encode(), method="PUT", headers={"Content-Type": "text/plain; version=0.0.4"})
    await asyncio.to_thread(urllib.request.urlopen, request, timeout=15)


async def main() -> None:
    run_id = os.environ["RUN_ID"]
    run_root = Path(os.environ["RESULTS_ROOT"]) / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    window_start = (started - timedelta(seconds=WINDOW_SECONDS)).isoformat()

    raw_by_provider, http_by_provider = await collect_actual_sources()
    transforms: dict[str, Callable[[dict[str, Any]], Any]] = {
        "kubernetes": normalize_kubernetes,
        "prometheus": normalize_prometheus,
        "loki": normalize_loki,
        "tempo": normalize_tempo,
    }
    normalized: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    raw_manifest: dict[str, Any] = {}
    for provider in PROVIDERS:
        raw = raw_by_provider[provider]
        result, elapsed, peak, samples = benchmark_transform(lambda raw=raw, provider=provider: transforms[provider](raw))
        normalized[provider] = result
        truth = provider_truth(provider, result)
        valid, reason = validate_agent_contract(provider, result)
        raw_bytes = compact_json(raw)
        normalized_bytes = compact_json(result)
        agent_body = compact_json({"agent_id": "payload-lab-agent", "lease_id": f"{provider}-lease", "status": "completed", "result": {"provider": provider, "evidence": result}})
        rows.append({
            "provider": provider,
            **http_by_provider[provider],
            "raw_json_bytes": len(raw_bytes),
            "normalized_bytes": len(normalized_bytes),
            "agent_body_bytes": len(agent_body),
            "original_items": count_raw(provider, raw),
            "returned_items": count_normalized(provider, result),
            "truth_expected": len(truth),
            "truth_retained": sum(truth.values()),
            "truth_retention_ratio": sum(truth.values()) / max(1, len(truth)),
            "truth_checks": truth,
            "contract_valid": valid,
            "contract_reason": reason,
            "transform_seconds_median": elapsed,
            "transform_seconds_samples": samples,
            "transform_peak_bytes": peak,
        })
        raw_manifest[provider] = {"sha256": sha256(raw_bytes), "bytes": len(raw_bytes)}

    body = ClusterEvidenceReceivedBody(
        workspace_id="payload-lab",
        cluster_id="payload-lab",
        agent_id="payload-lab-agent",
        source_id="same-window-experiment",
        window_start=window_start,
        evidence_key=f"payload-lab:{run_id}",
        correlation_id=run_id,
        kubernetes=normalized["kubernetes"],
        metrics=normalized["prometheus"],
        logs=normalized["loki"],
        traces=normalized["tempo"],
        collection_status=collection_status(),
        metadata={"run_id": run_id, "window_seconds": WINDOW_SECONDS, "providers": list(PROVIDERS)},
    )
    full = body.to_body()
    compact = compact_cluster_evidence_payload(body, run_id)
    reconciliation_failures: list[str] = []
    for provider in PROVIDERS:
        if provider not in raw_by_provider or provider not in normalized:
            reconciliation_failures.append(f"missing_provider:{provider}")
        if body.collection_status.get(provider, {}).get("status") != "success":
            reconciliation_failures.append(f"status_not_success:{provider}")
    if any(row["truth_retention_ratio"] < 1 for row in rows):
        reconciliation_failures.append("truth_signal_loss")
    if any(not row["contract_valid"] for row in rows):
        reconciliation_failures.append("provider_contract_rejected")
    if evidence_payload_size(full) > MAX_EVIDENCE_PAYLOAD_BYTES:
        reconciliation_failures.append("combined_evidence_over_1mib")

    safe_ai_input = {
        "correlation_id": run_id,
        "window_start": window_start,
        "collection_status": body.collection_status,
        "summary": compact.get("summary", {}),
        "signals": {provider: row["truth_checks"] for provider, row in zip(PROVIDERS, rows, strict=True)},
    }
    aggregate = {
        "run_id": run_id,
        "window_start": window_start,
        "window_seconds": WINDOW_SECONDS,
        "provider_count": len(PROVIDERS),
        "raw_provider_sum_bytes": sum(row["decoded_bytes"] for row in rows),
        "agent_body_bytes": sum(row["agent_body_bytes"] for row in rows),
        "full_evidence_bytes": evidence_payload_size(full),
        "safe_ai_input_bytes": len(compact_json(safe_ai_input)),
        "nats": await nats_measure(full, compact),
        "database": db_measure(full, compact),
        "reconciliation_failures": reconciliation_failures,
        "passed": not reconciliation_failures,
    }
    metrics = prometheus_lines(rows, aggregate)
    await push_metrics(metrics)

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "git_sha": os.environ.get("GIT_SHA", "unknown"),
        "started_at": started.isoformat(),
        "finished_at": utc_now().isoformat(),
        "environment": {"python": sys.version, "platform": sys.platform, "repeats": REPEATS},
        "limits": {"agent_evidence_bytes": MAX_EVIDENCE_PAYLOAD_BYTES, "window_seconds": WINDOW_SECONDS},
        "raw_sources": raw_manifest,
    }
    (run_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (run_root / "results.json").write_text(json.dumps({"providers": rows, "aggregate": aggregate}, ensure_ascii=False, indent=2) + "\n")
    (run_root / "summary.json").write_text(json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n")
    (run_root / "metrics.prom").write_text(metrics)
    (run_root / "evidence-full.sha256").write_text(f"{sha256(compact_json(full))}  evidence-full.json\n")
    with (run_root / "results.csv").open("w", newline="") as handle:
        fields = [key for key in rows[0] if key not in {"truth_checks", "transform_seconds_samples"}]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row[key] for key in fields} for row in rows)
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    if reconciliation_failures:
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
