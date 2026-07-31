"""관측 스택 준비도 — 실측 health 기반, 지어내지 않음."""

from __future__ import annotations

from domains.target.telemetry_readiness import telemetry_stack_view


def _wl(name: str, health: str = "healthy") -> dict[str, str]:
    return {"name": name, "kind": "Deployment", "namespace": "target", "health": health}


def test_no_telemetry_returns_none() -> None:
    # 에이전트만 있고 스택 설치 전 → 진행바 없음(None).
    view = telemetry_stack_view([_wl("cluster-agent"), _wl("optional-node-collector")])
    assert view is None


def test_full_stack_ready_is_complete() -> None:
    workloads = [
        _wl("minio"),
        _wl("prometheus"),
        _wl("prometheus-5ff8cd5865"),
        _wl("loki"),
        _wl("loki-gateway"),
        _wl("tempo"),
        _wl("opentelemetry-collector-agent"),
        _wl("cluster-agent"),
    ]
    view = telemetry_stack_view(workloads)
    assert view is not None
    assert view["ready_count"] == 5
    assert view["total"] == 5
    assert view["complete"] is True


def test_partial_install_reports_real_progress() -> None:
    # minio·prometheus 준비, loki 는 아직 미준비(pending) → 2/5, 미완료.
    workloads = [
        _wl("minio"),
        _wl("prometheus"),
        _wl("loki", health="pending"),
    ]
    view = telemetry_stack_view(workloads)
    assert view is not None
    assert view["ready_count"] == 2
    assert view["complete"] is False
    by_key = {c["key"]: c for c in view["components"]}
    assert by_key["loki"]["present"] is True
    assert by_key["loki"]["ready"] is False
    assert by_key["tempo"]["present"] is False


def test_prefix_match_does_not_cross_components() -> None:
    # 'tempo-x' 는 tempo 로 매칭되지만, 'prometheus-x' 가 loki 로 새지 않아야 한다.
    workloads = [_wl("prometheus-kube-state-metrics"), _wl("tempo-distributor")]
    view = telemetry_stack_view(workloads)
    assert view is not None
    by_key = {c["key"]: c for c in view["components"]}
    assert by_key["prometheus"]["ready"] is True
    assert by_key["tempo"]["ready"] is True
    assert by_key["loki"]["present"] is False


def test_status_fallback_when_health_absent() -> None:
    view = telemetry_stack_view([{"name": "minio", "status": "Running"}])
    assert view is not None
    assert view["components"][0]["ready"] is True
