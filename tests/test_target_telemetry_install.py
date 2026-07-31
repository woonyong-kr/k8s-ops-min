import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import HTTPException

from domains.target import router as target_router
from domains.target.router import (
    DEFAULT_OTEL_TRACES_URL,
    DEFAULT_PROMETHEUS_URL,
    install_command_for,
    install_telemetry_asset_by_token,
    install_telemetry_script_by_token,
    powershell_install_command_for,
)
from packages.contracts.gateway.requests import ClusterConnectRequest, TargetRegisterRequest

ROOT = Path(__file__).resolve().parents[1]


def script(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_target_registration_issues_receipt_then_installs_telemetry_before_agent() -> None:
    register = script("register-target.sh")

    install = register.index('bash "${SCRIPT_DIR}/install-telemetry.sh"')
    registration = register.index('echo "==> registering target in operations tool"')
    manifest = register.index('echo "==> applying generated target install manifest"')

    assert 'INSTALL_TELEMETRY="${INSTALL_TELEMETRY:-true}"' in register
    # API가 installer-scoped asset token을 먼저 발급하고, 이후 UI one-liner와
    # 동일하게 telemetry readiness를 통과한 뒤 agent manifest를 적용한다.
    assert registration < install < manifest
    assert "/integrations/prometheus" in register
    assert "clusterrole/cluster-agent-uninstall" in register
    assert "OTEL_TRACES_ENDPOINT" in register
    assert 'TARGET_CLUSTER_ID="${TARGET_CLUSTER_ID}"' in register
    assert 'WORKSPACE_ID="${WORKSPACE_ID}"' in register
    assert 'MANAGEMENT_API_BASE_URL="${API_BASE_URL}"' in register
    assert 'ALERTMANAGER_AGENT_TOKEN="${registration_agent_token}"' in register


def test_aws_target_registration_keeps_telemetry_required_by_default() -> None:
    aws_up = script("aws-up.sh")

    assert 'INSTALL_TELEMETRY="${INSTALL_TELEMETRY:-true}"' in aws_up
    assert 'INSTALL_TELEMETRY="${INSTALL_TELEMETRY}" \\' in aws_up


def test_telemetry_installer_pins_and_verifies_every_provider() -> None:
    installer = script("install-telemetry.sh")

    for version_name in (
        "PROMETHEUS_CHART_VERSION",
        "LOKI_CHART_VERSION",
        "TEMPO_CHART_VERSION",
        "OTEL_CHART_VERSION",
    ):
        assert f'--version "${{{version_name}}}"' in installer

    for release_name in (
        "PROMETHEUS_RELEASE",
        "LOKI_RELEASE",
        "TEMPO_RELEASE",
        "OTEL_RELEASE",
    ):
        assert f'require_release_workload "${{{release_name}}}"' in installer

    for service_name in (
        "prometheus",
        "loki-gateway",
        "tempo",
        "opentelemetry-collector",
    ):
        assert f"require_service_endpoints {service_name}" in installer

    otel_values = (ROOT / "deploy" / "target" / "opentelemetry.yaml").read_text(encoding="utf-8")
    assert "service:\n" in otel_values
    assert "  enabled: true" in otel_values
    powershell = script("install-telemetry.ps1")
    assert "require_tempo_runtime_bounds" in installer
    assert "Require-TempoRuntimeBounds" in powershell
    for source in (installer, powershell):
        assert "mem-ballast-size-mbs=0" in source
        assert "max_concurrent_queries: 4" in source
        assert "OpsiaSliFailureRatioHigh" in source
        assert "kyro-alertmanager-config" in source
        assert "send_resolved" in source
        assert "Bearer" in source
        assert "api/v2/status" in source
        for identity_label in (
            "opsia_namespace",
            "opsia_resource_kind",
            "opsia_resource_name",
            "opsia_service",
            "opsia_sli",
            "opsia_symptom",
        ):
            assert identity_label in source
        assert "opsia_observed_value" in source
        assert "opsia_threshold" in source
        assert "rollout restart" in source

    assert "umask 077" in installer
    assert 'chmod 600 "${config_file}"' in installer
    assert "trap cleanup EXIT" in installer
    assert 'create secret generic "${ALERTMANAGER_CONFIG_SECRET}"' in installer
    assert 'if [[ "${configured_count}" -eq 0 ]]' in installer
    assert 'Protect-TemporaryPath $configFile "600"' in powershell
    assert "Remove-Item -LiteralPath $assetDirectory" in powershell
    assert "if ($configured.Count -eq 0)" in powershell


def test_tempo_values_bound_ballast_blocks_queries_and_retention() -> None:
    tempo_values = yaml.safe_load(
        (ROOT / "deploy" / "target" / "tempo.yaml").read_text(encoding="utf-8")
    )
    tempo = tempo_values["tempo"]

    assert tempo["memBallastSizeMbs"] == 0
    assert tempo["retention"] == "6h"
    assert tempo["resources"]["requests"]["memory"] == "384Mi"
    assert tempo["resources"]["limits"]["memory"] == "1Gi"
    assert tempo["ingester"]["trace_idle_period"] == "10s"
    assert tempo["ingester"]["max_block_duration"] == "5m"
    assert tempo["querier"]["max_concurrent_queries"] == 4
    assert tempo["queryFrontend"]["search"]["concurrent_jobs"] == 32


def test_prometheus_values_define_generic_application_sli_alert() -> None:
    values = yaml.safe_load(
        (ROOT / "deploy" / "target" / "prometheus.yaml").read_text(encoding="utf-8")
    )
    server = values["server"]
    assert server["global"]["scrape_interval"] == "15s"
    assert server["global"]["evaluation_interval"] == "5s"

    rules = values["serverFiles"]["alerting_rules.yml"]["groups"][0]["rules"]
    recording_rule = next(
        item for item in rules if item.get("record") == "opsia_sli_failure_ratio"
    )
    recording_expr = recording_rule["expr"]
    assert "opsia_sli_requests_total" in recording_expr
    assert 'outcome="failure"' in recording_expr
    assert recording_expr.count(
        "sum by (namespace, resource_kind, resource_name, service, sli, symptom)"
    ) == 2
    assert "pod" not in recording_expr
    assert "instance" not in recording_expr
    assert recording_expr.count("[45s]") == 2

    rule = next(item for item in rules if item.get("alert") == "OpsiaSliFailureRatioHigh")
    assert rule["alert"] == "OpsiaSliFailureRatioHigh"
    assert "opsia_sli_failure_ratio{" in rule["expr"]
    for required_label in (
        "namespace",
        "resource_kind",
        "resource_name",
        "service",
        "sli",
        "symptom",
    ):
        assert f'{required_label}!=""' in rule["expr"]
    assert rule["expr"].strip().endswith("> 0.2")
    assert rule["for"] == "0s"
    assert rule["labels"]["opsia_symptom"] == "{{ $labels.symptom }}"
    assert rule["labels"]["opsia_resource_kind"] == "{{ $labels.resource_kind }}"
    assert rule["labels"]["opsia_resource_name"] == "{{ $labels.resource_name }}"
    assert rule["labels"]["opsia_namespace"] == "{{ $labels.namespace }}"
    assert rule["labels"]["opsia_service"] == "{{ $labels.service }}"
    assert rule["labels"]["opsia_sli"] == "{{ $labels.sli }}"

    rendered = (ROOT / "deploy" / "target" / "prometheus.yaml").read_text(encoding="utf-8")
    assert "DemoGame" not in rendered
    assert "find_game" not in rendered


def test_prometheus_sli_recording_rule_collapses_two_pods_to_one_identity() -> None:
    values = yaml.safe_load(
        (ROOT / "deploy" / "target" / "prometheus.yaml").read_text(encoding="utf-8")
    )
    rules = values["serverFiles"]["alerting_rules.yml"]["groups"][0]["rules"]
    expression = next(
        item["expr"] for item in rules if item.get("record") == "opsia_sli_failure_ratio"
    )
    identity_labels = (
        "namespace",
        "resource_kind",
        "resource_name",
        "service",
        "sli",
        "symptom",
    )
    group_clause = f"sum by ({', '.join(identity_labels)})"

    assert expression.count(group_clause) == 2
    assert 'outcome="failure"' in expression
    assert "pod" not in expression
    assert "instance" not in expression

    # Two Pods expose independent success/failure counter rates. The recording
    # rule deliberately groups both numerator and denominator by only the six
    # durable RCA labels, so rollout/scaling still produces one ratio series.
    pod_rates = [
        {
            **dict.fromkeys(identity_labels, ""),
            "namespace": "target",
            "resource_kind": "Deployment",
            "resource_name": "lobby",
            "service": "lobby",
            "sli": "admission",
            "symptom": "admission_failure_rate",
            "pod": "lobby-a",
            "outcome": "success",
            "rate": 80.0,
        },
        {
            **dict.fromkeys(identity_labels, ""),
            "namespace": "target",
            "resource_kind": "Deployment",
            "resource_name": "lobby",
            "service": "lobby",
            "sli": "admission",
            "symptom": "admission_failure_rate",
            "pod": "lobby-a",
            "outcome": "failure",
            "rate": 20.0,
        },
        {
            **dict.fromkeys(identity_labels, ""),
            "namespace": "target",
            "resource_kind": "Deployment",
            "resource_name": "lobby",
            "service": "lobby",
            "sli": "admission",
            "symptom": "admission_failure_rate",
            "pod": "lobby-b",
            "outcome": "success",
            "rate": 80.0,
        },
        {
            **dict.fromkeys(identity_labels, ""),
            "namespace": "target",
            "resource_kind": "Deployment",
            "resource_name": "lobby",
            "service": "lobby",
            "sli": "admission",
            "symptom": "admission_failure_rate",
            "pod": "lobby-b",
            "outcome": "failure",
            "rate": 20.0,
        },
    ]
    grouped: dict[tuple[str, ...], dict[str, float]] = {}
    for sample in pod_rates:
        identity = tuple(str(sample[label]) for label in identity_labels)
        totals = grouped.setdefault(identity, {"failure": 0.0, "total": 0.0})
        totals["total"] += float(sample["rate"])
        if sample["outcome"] == "failure":
            totals["failure"] += float(sample["rate"])

    assert len(grouped) == 1
    assert next(iter(grouped.values())) == {"failure": 40.0, "total": 200.0}
    assert next(iter(grouped.values()))["failure"] / next(iter(grouped.values()))["total"] == 0.2


def test_telemetry_installers_verify_recording_and_alert_rules_from_runtime_api() -> None:
    for source in (script("install-telemetry.sh"), script("install-telemetry.ps1")):
        assert "api/v1/rules" in source
        assert "opsia_sli_failure_ratio" in source
        assert "opsia_sli_requests_total" in source
        assert 'outcome="failure"' in source
        assert "sumby(namespace,resource_kind,resource_name,service,sli,symptom)" in source
        assert "OpsiaSliFailureRatioHigh" in source
        assert "credentials:" in source
        assert "<secret>" in source


def test_alertmanager_runtime_validation_accepts_v033_redacted_yaml() -> None:
    status = {
        "config": {
            "original": """\
global:
  resolve_timeout: 5m
route:
  receiver: kyro-rca
receivers:
- name: kyro-rca
  webhook_configs:
  - send_resolved: true
    url: <secret>
    http_config:
      authorization:
        type: Bearer
        credentials: <secret>
"""
        }
    }
    runtime_config = status["config"]["original"]

    shell = script("install-telemetry.sh")
    shell_block = shell.split("    required = (\n", 1)[1].split(
        "    )\n    valid = ", 1
    )[0]
    shell_patterns = re.findall(r'r"([^"]+)"', shell_block)

    powershell = script("install-telemetry.ps1")
    powershell_block = powershell.split("        $required = @(\n", 1)[1].split(
        "        )\n        $validRuntime", 1
    )[0]
    powershell_patterns = re.findall(r"'([^']+)'", powershell_block)

    assert len(shell_patterns) == 7
    assert len(powershell_patterns) == 7
    assert all(re.search(pattern, runtime_config) for pattern in shell_patterns)
    assert all(re.search(pattern, runtime_config) for pattern in powershell_patterns)


def test_agent_api_proxy_exposes_authenticated_alertmanager_webhook_only() -> None:
    source = (ROOT / "deploy" / "management" / "agent-api-proxy.yaml").read_text(
        encoding="utf-8"
    )

    assert "location = /api/webhooks/alertmanager {" in source
    assert (
        "proxy_pass "
        "http://api-gateway.management.svc.cluster.local:8000/webhooks/alertmanager;"
    ) in source
    assert "location ^~ /api/webhooks/" not in source


def test_dev_deploy_reconciles_and_reloads_exact_agent_api_proxy_runtime() -> None:
    workflow = (ROOT / ".github" / "workflows" / "dev-deploy.yml").read_text(
        encoding="utf-8"
    )
    step = workflow.split(
        "      - name: Reconcile agent API proxy runtime\n",
        1,
    )[1].split("      - name: ", 1)[0]

    assert "--kind ConfigMap" in step
    assert "--name agent-api-proxy-config" in step
    assert "--kind Deployment" in step
    assert "--kind PodDisruptionBudget" in step
    assert "--kind Service" in step
    assert step.count("--name agent-api-proxy") == 4
    assert 'annotations:{"opsia.dev/config-sha256":$checksum}' in step
    assert "patch deployment agent-api-proxy" in step
    assert "rollout status deployment/agent-api-proxy --timeout=300s" in step
    assert "apply -f deploy/management" not in step


def test_ui_connect_command_installs_telemetry_before_agent_manifest() -> None:
    payload = TargetRegisterRequest(
        cluster_id="cluster-1",
        management_base_url="https://ops.example.test/api",
    )

    command = install_command_for(payload, "agent-token")

    telemetry = command.index("/install/agent-token/telemetry/bash")
    manifest = command.index("/install/agent-token | kubectl apply")
    assert telemetry < manifest
    assert "TELEMETRY_ASSET_BASE_URL=" in command
    assert "TARGET_CLUSTER_ID=cluster-1" in command
    assert "WORKSPACE_ID=default" in command
    assert "MANAGEMENT_API_BASE_URL=https://ops.example.test/api" in command
    assert "ALERTMANAGER_AGENT_TOKEN=agent-token" in command
    assert '-o "$telemetry_script" || exit 1' in command
    assert 'bash "$telemetry_script" || exit 1' in command
    assert "cluster-agent-uninstall" in command
    assert "kubectl config current-context" in command


def test_ui_powershell_connect_command_installs_telemetry_before_agent_manifest() -> None:
    payload = TargetRegisterRequest(
        cluster_id="cluster-1",
        management_base_url="https://ops.example.test/api",
    )

    command = powershell_install_command_for(payload, "agent-token")

    telemetry = command.index("/install/agent-token/telemetry/powershell")
    manifest = command.index("/install/agent-token'")
    assert telemetry < manifest
    assert "-AssetBaseUrl" in command
    assert "-ClusterId 'cluster-1'" in command
    assert "-WorkspaceId 'default'" in command
    assert "-ManagementApiBaseUrl 'https://ops.example.test/api'" in command
    assert "-AgentToken $kyroAgentToken" in command
    assert "Remove-Variable kyroAgentToken" in command
    assert "cluster-agent-uninstall" in command


def test_connect_command_normalizes_management_api_base_for_telemetry() -> None:
    payload = TargetRegisterRequest(
        cluster_id="cluster-1",
        workspace_id="workspace-1",
        management_base_url="https://ops.example.test/",
    )

    shell = install_command_for(payload, "agent-token")
    powershell = powershell_install_command_for(payload, "agent-token")

    for command in (shell, powershell):
        assert "https://ops.example.test/api" in command
        assert "workspace-1" in command


def test_management_agent_install_does_not_install_target_telemetry() -> None:
    payload = TargetRegisterRequest(
        cluster_id="management-cluster",
        cluster_role="management",
        management_base_url="https://ops.example.test/api",
    )

    shell = install_command_for(payload, "agent-token")
    powershell = powershell_install_command_for(payload, "agent-token")

    assert "/telemetry/" not in shell
    assert "/telemetry/" not in powershell
    assert "cluster-agent-uninstall" not in shell
    assert "cluster-agent-uninstall" not in powershell
    assert "/install/agent-token" in shell
    assert "/install/agent-token" in powershell


def test_connect_defaults_route_all_four_telemetry_signals() -> None:
    assert DEFAULT_PROMETHEUS_URL == "http://prometheus.target.svc.cluster.local:9090"
    assert DEFAULT_OTEL_TRACES_URL == ("http://opentelemetry-collector.target.svc:4318/v1/traces")

    router_source = (ROOT / "src" / "domains" / "target" / "router.py").read_text(encoding="utf-8")
    assert "await update_prometheus_integration(" in router_source
    assert "otel_traces_endpoint=DEFAULT_OTEL_TRACES_URL" in router_source


def test_remote_install_artifacts_are_packaged_in_service_image() -> None:
    installer = script("install-telemetry.sh")
    powershell = script("install-telemetry.ps1")
    dockerfile = (ROOT / "src" / "services" / "Dockerfile").read_text(encoding="utf-8")

    assert "TELEMETRY_ASSET_BASE_URL" in installer
    assert "AssetBaseUrl" in powershell
    assert "RNGCryptoServiceProvider" in powershell
    assert "RandomNumberGenerator]::Fill" not in powershell
    assert "Convert]::ToHexString" not in powershell
    assert "scripts/install-telemetry.sh ./scripts/install-telemetry.sh" in dockerfile
    assert "scripts/install-telemetry.ps1 ./scripts/install-telemetry.ps1" in dockerfile
    assert "deploy/target ./deploy/target" in dockerfile


class InstallerArtifactDb:
    def authenticate_cluster_agent(self, _token_hash: str) -> dict[str, str]:
        return {"workspace_id": "workspace-1", "cluster_id": "cluster-1"}

    def get_cluster_registration_install_credentials(
        self,
        workspace_id: str,
        cluster_id: str,
    ) -> dict[str, str]:
        assert workspace_id == "workspace-1"
        assert cluster_id == "cluster-1"
        return {"cluster_id": cluster_id}


def test_installer_token_serves_only_allowlisted_telemetry_artifacts() -> None:
    db = InstallerArtifactDb()

    shell = asyncio.run(install_telemetry_script_by_token("agent-token", "bash", db=db))
    asset = asyncio.run(install_telemetry_asset_by_token("agent-token", "prometheus.yaml", db=db))
    tempo_asset = asyncio.run(
        install_telemetry_asset_by_token("agent-token", "tempo.yaml", db=db)
    )

    assert b"helm upgrade --install" in shell.body
    assert b"fullnameOverride: prometheus" in asset.body
    assert b"memBallastSizeMbs: 0" in tempo_asset.body
    assert b"max_concurrent_queries: 4" in tempo_asset.body

    with pytest.raises(HTTPException) as exc:
        asyncio.run(install_telemetry_asset_by_token("agent-token", "../secrets", db=db))
    assert exc.value.status_code == 404


def test_connect_revokes_new_registration_when_prometheus_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revoked: list[tuple[str, str]] = []

    class Db:
        def unregister_target_cluster(self, workspace_id: str, cluster_id: str) -> bool:
            revoked.append((workspace_id, cluster_id))
            return True

    async def register(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            cluster_id="cluster-1",
            install_command="install",
            powershell_install_command="install-powershell",
            connect_expires_at="2026-07-24T05:00:00+00:00",
        )

    async def fail_prometheus(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("prometheus setup failed")

    monkeypatch.setattr(target_router, "require_unique_cluster_display_name", lambda *_: None)
    monkeypatch.setattr(target_router, "register_target", register)
    monkeypatch.setattr(target_router, "update_prometheus_integration", fail_prometheus)

    with pytest.raises(RuntimeError, match="prometheus setup failed"):
        asyncio.run(
            target_router.connect_cluster(
                ClusterConnectRequest(name="cluster"),
                current=SimpleNamespace(workspace_id="workspace-1"),
                db=Db(),
                events=object(),
                operation_events=object(),
            )
        )

    assert revoked == [("workspace-1", "cluster-1")]


def test_loki_object_store_declares_every_required_bucket() -> None:
    loki_values = (ROOT / "deploy" / "target" / "loki.yaml").read_text(encoding="utf-8")
    minio_manifest = (ROOT / "deploy" / "target" / "minio.yaml").read_text(encoding="utf-8")

    for bucket in ("loki-chunks", "loki-ruler", "loki-admin"):
        assert bucket in loki_values
        assert f"local/{bucket}" in minio_manifest

    assert "minio/mc:RELEASE.2025-05-21T01-59-54Z" in minio_manifest
