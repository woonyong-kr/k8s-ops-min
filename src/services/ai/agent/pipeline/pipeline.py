from __future__ import annotations

from dataclasses import dataclass, field, replace

from domains.rca.events import (
    ClusterEvidenceReceivedBody,
    Evidence,
    EvidenceBundleBuiltBody,
    IncidentDetectedBody,
    RcaCandidatesEvaluatedBody,
    RcaCandidatesPlannedBody,
    RcaCompletedBody,
)
from packages.contracts.event_bus.bodies import EventBody
from services.ai.agent.pipeline.causes import CauseEvaluator, CausePlanner, RootCauseAnalyzer
from services.ai.agent.pipeline.evidence import EvidenceBuilder
from services.ai.agent.pipeline.incident import EvidenceBundler, IncidentDetector
from services.ai.agent.recovery.engine import RecoveryPlanner


@dataclass(frozen=True)
class EvidencePipeline:
    builder: EvidenceBuilder = field(default_factory=EvidenceBuilder)

    @property
    def kind(self) -> str:
        return self.builder.kind

    def build_evidence(self, evt: ClusterEvidenceReceivedBody, correlation_id: str) -> Evidence:
        return self.builder.build_evidence(evt, correlation_id)


@dataclass(frozen=True)
class IncidentPipelineBodies:
    detected_body: IncidentDetectedBody
    next_body: EventBody


@dataclass(frozen=True)
class IncidentPipeline:
    detector: IncidentDetector = field(default_factory=IncidentDetector)
    bundler: EvidenceBundler = field(default_factory=EvidenceBundler)

    def build_bodies(self, evidence: Evidence, correlation_id: str) -> IncidentPipelineBodies:
        detected = self.detector.detect_body(evidence, correlation_id)
        next_context = replace(detected, evidence=evidence)
        return IncidentPipelineBodies(
            detected_body=detected,
            next_body=self.bundler.build_body(next_context),
        )


@dataclass(frozen=True)
class CausePlanningPipeline:
    planner: CausePlanner = field(default_factory=CausePlanner)

    def plan_body(self, evt: EvidenceBundleBuiltBody) -> RcaCandidatesPlannedBody:
        return self.planner.plan_body(evt)

    def plan_bodies(self, evt: EvidenceBundleBuiltBody) -> tuple[EventBody, ...]:
        return self.planner.plan_bodies(evt)


@dataclass(frozen=True)
class CauseEvaluationPipeline:
    evaluator: CauseEvaluator = field(default_factory=CauseEvaluator)

    def evaluate_body(self, evt: RcaCandidatesPlannedBody) -> EventBody:
        return self.evaluator.evaluate_body(evt)


@dataclass(frozen=True)
class RcaCompletionPipeline:
    analyzer: RootCauseAnalyzer = field(default_factory=RootCauseAnalyzer)

    def complete_body(self, evt: RcaCandidatesEvaluatedBody) -> EventBody:
        return self.analyzer.complete_body(evt)


@dataclass(frozen=True)
class RecoveryPlanningPipeline:
    planner: RecoveryPlanner = field(default_factory=RecoveryPlanner)

    def plan_body(self, evt: RcaCompletedBody) -> EventBody:
        return self.planner.plan_body(evt)
