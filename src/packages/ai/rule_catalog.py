"""RCA 룰 YAML 검증 스키마 — 도메인/서비스가 공유하는 순수 계약."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MATCHER_KEYS = ("fact", "log_pattern", "event_pattern")


@dataclass(frozen=True)
class CatalogValidationIssue:
    code: str
    detail: str
    line: int | None = None


@dataclass(frozen=True)
class CatalogCandidateSpec:
    candidate_id: str
    title: str
    description: str
    expected_evidence: tuple[str, ...]
    checks: tuple[str, ...]
    signals: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class CatalogRuleSpec:
    rule_id: str
    symptoms: tuple[str, ...]
    required_sources: tuple[str, ...]
    candidates: tuple[CatalogCandidateSpec, ...]


@dataclass(frozen=True)
class CatalogValidationResult:
    valid: bool
    rules: tuple[CatalogRuleSpec, ...] = ()
    errors: tuple[CatalogValidationIssue, ...] = ()


class CatalogSignalMatcherModel(BaseModel):
    """판별 신호 matcher 스키마 — fact/log_pattern/event_pattern 중 정확히 하나."""

    model_config = ConfigDict(extra="forbid")

    fact: str | None = Field(default=None, min_length=1)
    log_pattern: str | None = Field(default=None, min_length=1)
    event_pattern: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def exactly_one_matcher(self) -> CatalogSignalMatcherModel:
        provided = [key for key in MATCHER_KEYS if getattr(self, key) is not None]
        if len(provided) != 1:
            raise ValueError(
                f"signal matcher 는 {'/'.join(MATCHER_KEYS)} 중 정확히 하나여야 합니다"
            )
        return self

    def to_payload(self) -> dict[str, str]:
        key = next(key for key in MATCHER_KEYS if getattr(self, key) is not None)
        return {key: str(getattr(self, key))}


class CatalogSignalGroupModel(BaseModel):
    """판별 신호 그룹 스키마 — any_of 중 하나라도 매칭되면 그룹 충족."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    any_of: list[CatalogSignalMatcherModel] = Field(min_length=1)

    def to_payload(self) -> dict[str, object]:
        return {"id": self.id, "any_of": [matcher.to_payload() for matcher in self.any_of]}


class CatalogCandidateModel(BaseModel):
    """YAML 원인 후보 스키마."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    expected_evidence: list[str] = Field(min_length=1)
    checks: list[str] = Field(min_length=1)
    signals: list[CatalogSignalGroupModel] = Field(default_factory=list)

    def to_spec(self) -> CatalogCandidateSpec:
        return CatalogCandidateSpec(
            candidate_id=self.candidate_id,
            title=self.title,
            description=self.description,
            expected_evidence=tuple(self.expected_evidence),
            checks=tuple(self.checks),
            signals=tuple(group.to_payload() for group in self.signals),
        )


class CatalogRuleModel(BaseModel):
    """YAML 룰 스키마 — 증상 매칭 조건 + 필수 근거 소스 + 원인 후보 목록."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    symptoms: list[str] = Field(min_length=1)
    required_sources: list[str] = Field(min_length=1)
    candidates: list[CatalogCandidateModel] = Field(min_length=1)

    def to_spec(self) -> CatalogRuleSpec:
        return CatalogRuleSpec(
            rule_id=self.id,
            symptoms=tuple(self.symptoms),
            required_sources=tuple(self.required_sources),
            candidates=tuple(candidate.to_spec() for candidate in self.candidates),
        )


class CatalogFileModel(BaseModel):
    """카탈로그 파일 루트 스키마 — rules 목록 하나."""

    model_config = ConfigDict(extra="forbid")

    rules: list[CatalogRuleModel] = Field(min_length=1)


def validate_catalog_yaml(raw_text: str) -> CatalogValidationResult:
    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        line = int(mark.line) + 1 if mark is not None else None
        return CatalogValidationResult(
            valid=False,
            errors=(
                CatalogValidationIssue(
                    code="yaml_parse_error",
                    detail=f"RCA 룰 YAML 파싱 실패: {error}",
                    line=line,
                ),
            ),
        )
    try:
        model = CatalogFileModel.model_validate(raw)
    except ValidationError as error:
        return CatalogValidationResult(
            valid=False,
            errors=(
                CatalogValidationIssue(
                    code="schema_error",
                    detail=f"RCA 룰 스키마 위반: {error}",
                ),
            ),
        )
    seen_ids: set[str] = set()
    rules: list[CatalogRuleSpec] = []
    for rule in model.rules:
        if rule.id in seen_ids:
            return CatalogValidationResult(
                valid=False,
                errors=(
                    CatalogValidationIssue(
                        code="duplicate_rule_id",
                        detail=f"RCA 룰 id 중복: '{rule.id}'",
                    ),
                ),
            )
        seen_ids.add(rule.id)
        rules.append(rule.to_spec())
    return CatalogValidationResult(valid=True, rules=tuple(rules))
