"""control_namespaces → 에이전트 수집 정책 반영 — 관리 네임스페이스가 화면에 보이게.

종전에는 standard 프로파일이 에이전트 자신의 네임스페이스(target)만 수집해,
control 네임스페이스(sandbox 등)에 배포된 워크로드가 인벤토리에 나타나지 않았다.
"""

from __future__ import annotations

from conftest import ROOT, load_file

from domains.target.evidence_policy import (
    control_namespace_tuple,
    default_agent_policy,
    evidence_provider_queries,
    preserve_server_owned_evidence_queries,
)
from domains.target.management_guard import freeze_management_policy, refresh_management_policy


def _kubernetes_namespaces(queries: list[dict[str, object]]) -> list[str]:
    namespaces: list[str] = []
    for query in queries:
        provenance = query.get("provenance")
        if not isinstance(provenance, dict):
            continue
        for namespace in provenance.get("namespaces") or []:
            namespaces.append(str(namespace))
    return namespaces


def test_control_namespace_tuple_normalizes_commas_and_spaces() -> None:
    assert control_namespace_tuple("sandbox,color-turf") == ("sandbox", "color-turf")
    assert control_namespace_tuple("  sandbox  color-turf ") == ("sandbox", "color-turf")
    assert control_namespace_tuple("sandbox,sandbox") == ("sandbox",)
    assert control_namespace_tuple(None) == ()


def test_standard_profile_collects_control_namespaces() -> None:
    queries = evidence_provider_queries(
        "kubernetes",
        cluster_id="c-1",
        evidence_profile="standard",
        control_namespaces=("sandbox", "color-turf"),
    )
    namespaces = _kubernetes_namespaces(queries)
    assert "target" in namespaces
    assert "sandbox" in namespaces
    assert "color-turf" in namespaces
    names = [str(query["name"]) for query in queries]
    # 이름 슬러그: 하이픈 → 언더스코어(정책 이름 규칙 유지).
    assert "color_turf_namespace_snapshot" in names


def test_management_profile_does_not_promote_control_plane_events() -> None:
    queries = evidence_provider_queries(
        "kubernetes",
        cluster_id="management-server",
        evidence_profile="management",
    )
    names = [str(query["name"]) for query in queries]

    assert "management_namespace_snapshot" in names
    assert "cluster_api_discovery" in names
    assert "cluster_access_snapshot" in names
    assert "cluster_wide_event_capture" not in names
    assert all(query.get("collection_scope") != "cluster_events" for query in queries)


def test_management_policy_freeze_removes_legacy_event_capture() -> None:
    policy = default_agent_policy(
        cluster_id="management-server",
        cluster_role="management",
    )
    payload = policy.model_dump()
    payload["evidence"]["providers"]["kubernetes"]["queries"].append(
        {
            "source": "kubernetes",
            "name": "legacy_event_capture",
            "description": "legacy management event capture",
            "query": "*",
            "collection_scope": "cluster_events",
            "provenance": {
                "cluster_id": "management-server",
                "backend_scope": "cluster_local",
                "query_scope": "cluster",
                "evidence_profile": "management",
                "namespaces": [],
                "required_matchers": [],
            },
        }
    )
    policy = type(policy).model_validate(payload)
    assert any(
        query.get("collection_scope") == "cluster_events"
        for query in policy.evidence.providers["kubernetes"].queries
    )

    frozen = freeze_management_policy(policy)

    assert all(
        query.get("collection_scope") != "cluster_events"
        for query in frozen.evidence.providers["kubernetes"].queries
    )
    assert refresh_management_policy(policy).generation == policy.generation + 1
    assert refresh_management_policy(frozen).generation == frozen.generation


def test_metrics_collect_raw_exact_active_session_continuity_series() -> None:
    queries = evidence_provider_queries(
        "metrics",
        cluster_id="c-1",
        evidence_profile="standard",
    )
    query = next(item for item in queries if item["name"] == "opsia_continuity_active_sessions")

    assert query["query"] == (
        'opsia_continuity_active_sessions{namespace!="",resource_kind!="",'
        'resource_name!="",continuity_id!="",pod_uid!=""}'
    )
    assert "sum by" not in str(query["query"])
    assert "max by" not in str(query["query"])


def test_recovery_continuity_label_survives_bounded_metadata_snapshot() -> None:
    kubernetes_utils = load_file(
        ROOT
        / "src"
        / "services"
        / "target"
        / "cluster-agent"
        / "providers"
        / "kubernetes_utils.py",
        "test_recovery_kubernetes_utils",
    )
    labels = {f"chart.example/label-{index:02d}": str(index) for index in range(20)}
    labels["opsia.dev/recovery-continuity"] = "protected"

    safe = kubernetes_utils.safe_metadata_labels(labels)

    assert len(safe) == 12
    assert safe["opsia.dev/recovery-continuity"] == "protected"


def test_standard_profile_collects_control_namespace_logs() -> None:
    queries = evidence_provider_queries(
        "logs",
        cluster_id="c-1",
        evidence_profile="standard",
        control_namespaces=("sandbox", "color-turf"),
    )
    names = [str(query["name"]) for query in queries]
    assert "sandbox_namespace_related_logs" in names
    assert "color_turf_namespace_related_logs" in names
    assert any(query["query"] == '{k8s_namespace_name="sandbox"}' for query in queries)


def test_metadata_collects_target_and_configured_control_namespaces() -> None:
    queries = evidence_provider_queries(
        "metadata",
        cluster_id="c-1",
        evidence_profile="standard",
        control_namespaces=("sandbox", "color-turf", "target", "sandbox"),
    )

    assert [query["query"] for query in queries] == [
        "target",
        "sandbox",
        "color-turf",
    ]
    assert queries[0]["provenance"]["namespaces"] == ["target"]
    assert queries[1]["provenance"]["namespaces"] == ["sandbox"]
    assert queries[2]["provenance"]["namespaces"] == ["color-turf"]
    serialized = "\n".join(str(query) for query in queries)
    assert "api-server" not in serialized
    assert "find_game_rejected" not in serialized


def test_target_profile_enables_cluster_local_tempo_query() -> None:
    queries = evidence_provider_queries(
        "traces",
        cluster_id="c-1",
        evidence_profile="standard",
    )
    assert [query["name"] for query in queries] == ["cluster_recent_traces"]
    assert queries[0]["query"] == "{}"
    assert queries[0]["range_seconds"] == 15 * 60
    assert queries[0]["provenance"]["backend_scope"] == "cluster_local"


def test_demo_profile_does_not_duplicate_covered_namespaces() -> None:
    queries = evidence_provider_queries(
        "kubernetes",
        cluster_id="c-1",
        evidence_profile="demo",
        control_namespaces=("sandbox", "game-live"),
    )
    names = [str(query["name"]) for query in queries]
    assert names.count("sandbox_namespace_snapshot") == 1  # demo 기본과 중복 금지
    assert "game_live_namespace_snapshot" in names


def test_demo_log_policy_collects_configured_namespace_without_app_hardcoding() -> None:
    queries = evidence_provider_queries(
        "logs",
        cluster_id="c-1",
        evidence_profile="demo",
        control_namespaces=("sandbox",),
    )
    by_name = {str(query["name"]): query for query in queries}

    related = by_name["sandbox_namespace_related_logs"]
    assert related["query"] == '{k8s_namespace_name="sandbox"}'
    assert related["provenance"]["namespaces"] == ["sandbox"]
    assert related["provenance"]["cluster_id"] == "c-1"
    structured = by_name["sandbox_namespace_structured_rejections"]
    assert structured["query"] == (
        '{k8s_namespace_name="sandbox"} | json | outcome="rejected"'
    )
    assert structured["provenance"]["namespaces"] == ["sandbox"]
    assert structured["provenance"]["cluster_id"] == "c-1"
    serialized = "\n".join(str(query["query"]) for query in queries)
    assert "api-server" not in serialized
    assert "find_game_rejected" not in serialized
    assert "rate_limited" not in serialized
    assert "no_room" not in serialized


def test_default_agent_policy_threads_control_namespaces() -> None:
    policy = default_agent_policy(
        cluster_id="c-1",
        cluster_role="target",
        control_namespaces=("sandbox",),
    )
    kubernetes = policy.evidence.providers["kubernetes"]
    namespaces = _kubernetes_namespaces(list(kubernetes.queries))
    assert "sandbox" in namespaces


def test_empty_control_namespaces_keeps_existing_query_set() -> None:
    baseline = evidence_provider_queries(
        "kubernetes", cluster_id="c-1", evidence_profile="standard"
    )
    with_empty = evidence_provider_queries(
        "kubernetes",
        cluster_id="c-1",
        evidence_profile="standard",
        control_namespaces=(),
    )
    assert [q["name"] for q in baseline] == [q["name"] for q in with_empty]


def test_policy_update_cannot_erase_server_owned_collection_queries() -> None:
    """A partial policy PUT must not disconnect a healthy target from inventory."""

    policy = default_agent_policy(
        cluster_id="battlegrounds-8352",
        evidence_profile="demo",
        control_namespaces=("sandbox",),
    )
    payload = policy.model_dump()
    payload["evidence"]["providers"]["kubernetes"]["queries"] = []
    payload["evidence"]["providers"]["kubernetes"]["enabled"] = False
    payload["evidence"]["providers"]["logs"]["queries"].append(
        {
            "source": "loki",
            "name": "operator_custom_logs",
            "description": "Operator-owned log query.",
            "query": '{k8s_namespace_name="custom"}',
        }
    )

    repaired = preserve_server_owned_evidence_queries(
        type(policy).model_validate(payload),
        cluster_id="battlegrounds-8352",
        evidence_profile="demo",
        control_namespaces=("sandbox",),
    )

    kubernetes = repaired.evidence.providers["kubernetes"]
    names = {str(query["name"]) for query in kubernetes.queries}
    assert {
        "target_namespace_snapshot",
        "sandbox_namespace_snapshot",
        "cluster_api_discovery",
        "cluster_access_snapshot",
        "cluster_wide_event_capture",
    }.issubset(names)
    # Disabling a provider remains an operator choice; its canonical contract is
    # retained so a later re-enable cannot resume with an empty collection scope.
    assert kubernetes.enabled is False
    assert any(
        query.get("name") == "operator_custom_logs"
        for query in repaired.evidence.providers["logs"].queries
    )
