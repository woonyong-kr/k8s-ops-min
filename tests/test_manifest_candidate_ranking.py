"""루트 후보 랭킹 — 진입점(kustomization/Chart)을 상위로 올려 자동 선택·추천."""

from __future__ import annotations

from domains.gitops.repository_discovery import rank_manifest_candidates
from packages.contracts.gateway.responses import RepositoryManifestCandidate as C


def _cand(path: str, source_type: str) -> C:
    return C(path=path, source_type=source_type, display_name=path.rsplit("/", 1)[-1], reason="")


def test_kustomize_root_ranked_first_and_recommended() -> None:
    ranked = rank_manifest_candidates(
        [
            _cand("app/deployment.yaml", "raw-yaml"),
            _cand("overlays/prod/kustomization.yaml", "kustomize"),
            _cand("chart/Chart.yaml", "helm"),
        ]
    )
    assert ranked[0].source_type == "kustomize"
    assert ranked[0].reason.startswith("추천")
    assert [c.source_type for c in ranked] == ["kustomize", "helm", "raw-yaml"]


def test_standard_dir_beats_deep_random_path() -> None:
    ranked = rank_manifest_candidates(
        [
            _cand("misc/tools/thing.yaml", "raw-yaml"),
            _cand("deploy/deployment.yaml", "raw-yaml"),
        ]
    )
    # 표준 경로 + 추천 파일명이 상위.
    assert ranked[0].path == "deploy/deployment.yaml"


def test_shallower_path_preferred_when_otherwise_equal() -> None:
    ranked = rank_manifest_candidates(
        [
            _cand("a/b/c/svc.yaml", "raw-yaml"),
            _cand("svc.yaml", "raw-yaml"),
        ]
    )
    assert ranked[0].path == "svc.yaml"


def test_reason_filled_for_each_type() -> None:
    ranked = rank_manifest_candidates(
        [_cand("k/kustomization.yaml", "kustomize"), _cand("x/values.yaml", "helm")]
    )
    reasons = {c.source_type: c.reason for c in ranked}
    assert "Kustomize" in reasons["kustomize"]
    assert "Helm" in reasons["helm"]


def test_empty_list_is_safe() -> None:
    assert rank_manifest_candidates([]) == []
