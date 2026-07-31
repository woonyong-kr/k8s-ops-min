"""RCA 원인 룰 YAML 카탈로그 로더 — 룰을 코드가 아닌 데이터(YAML)로 관리한다.

카탈로그 위치: `src/services/ai/agent/causes/catalog/*.yaml` (파일명 정렬 순서로 로딩).
잘못된 YAML·스키마 위반·중복 rule id 는 기동(카탈로그 임포트) 시점에
`CauseCatalogError` 로 즉시 실패시켜, 잘못된 룰이 조용히 무시되는 일을 막는다.
"""

from __future__ import annotations

from pathlib import Path

from packages.ai.rule_catalog import (
    CatalogCandidateSpec as CatalogCandidateRuleSpec,
)
from packages.ai.rule_catalog import (
    CatalogRuleSpec,
    validate_catalog_yaml,
)
from services.ai.agent.playbooks.cause import (
    CAUSE_PROFILES,
    CauseCandidateSpec,
    CauseProfile,
)

CATALOG_DIR = Path(__file__).resolve().parent / "catalog"
CATALOG_PATTERNS = ("*.yaml", "*.yml")


class CauseCatalogError(RuntimeError):
    """RCA 룰 카탈로그 로딩 실패 — 기동 시점에 즉시 중단시키는 오류."""


def cause_candidate_from_catalog(spec: CatalogCandidateRuleSpec) -> CauseCandidateSpec:
    return CauseCandidateSpec(
        candidate_id=spec.candidate_id,
        title=spec.title,
        description=spec.description,
        expected_evidence=spec.expected_evidence,
        checks=spec.checks,
        signals=spec.signals,
    )


def cause_profile_from_catalog(rule: CatalogRuleSpec) -> CauseProfile:
    return CauseProfile(
        symptoms=rule.symptoms,
        required_sources=rule.required_sources,
        candidate_specs=tuple(
            cause_candidate_from_catalog(candidate) for candidate in rule.candidates
        ),
        rule_id=rule.rule_id,
    )


def parse_catalog_file(path: Path) -> tuple[CauseProfile, ...]:
    """카탈로그 파일 1개를 파싱·검증해 프로파일 튜플로 변환한다."""
    result = validate_catalog_yaml(path.read_text(encoding="utf-8"))
    if not result.valid:
        issue = result.errors[0]
        prefix = (
            "RCA 룰 카탈로그 YAML 파싱 실패"
            if issue.code == "yaml_parse_error"
            else "RCA 룰 카탈로그 스키마 위반"
        )
        raise CauseCatalogError(f"{prefix}: {path.name} — {issue.detail}")
    return tuple(cause_profile_from_catalog(rule) for rule in result.rules)


def load_catalog_profiles(catalog_dir: Path | None = None) -> tuple[CauseProfile, ...]:
    """카탈로그 디렉터리의 모든 YAML 룰을 로딩한다(순수 함수, 파일명 정렬 순서).

    카탈로그 안에서 rule id 가 중복되면 `CauseCatalogError` 를 던진다.
    """
    directory = CATALOG_DIR if catalog_dir is None else catalog_dir
    paths = sorted(path for pattern in CATALOG_PATTERNS for path in directory.glob(pattern))
    profiles: list[CauseProfile] = []
    seen_ids: dict[str, str] = {}
    for path in paths:
        for profile in parse_catalog_file(path):
            assert profile.rule_id is not None  # CatalogRuleModel.id 가 보장
            owner = seen_ids.get(profile.rule_id)
            if owner is not None:
                raise CauseCatalogError(
                    f"RCA 룰 id 중복: '{profile.rule_id}' ({owner} ↔ {path.name}) — "
                    "카탈로그 룰 id 는 고유해야 합니다."
                )
            seen_ids[profile.rule_id] = path.name
            profiles.append(profile)
    return tuple(profiles)


def register_catalog_profiles(catalog_dir: Path | None = None) -> None:
    """카탈로그 룰을 룰 레지스트리(`CAUSE_PROFILES`)에 병합한다.

    이미 등록된 코드/카탈로그 룰과 rule id 가 겹치면 `CauseCatalogError` 로 즉시 실패한다.
    """
    profiles = load_catalog_profiles(catalog_dir)
    existing_ids = {p.rule_id for p in CAUSE_PROFILES if p.rule_id is not None}
    for profile in profiles:
        if profile.rule_id in existing_ids:
            raise CauseCatalogError(
                f"RCA 룰 id 중복: '{profile.rule_id}' — 이미 등록된 룰과 겹칩니다."
            )
    CAUSE_PROFILES.extend(profiles)
