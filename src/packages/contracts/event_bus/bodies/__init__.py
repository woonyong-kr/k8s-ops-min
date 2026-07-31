"""이벤트 body 계약의 공개 import 표면.

기존 `packages.contracts.event_bus.bodies` import 경로를 유지하되 도메인 body 는 lazy 로딩.
도메인 `events.py` 가 `bodies.base` 를 import 하므로 여기서 eager re-export 하면 순환 import 발생.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from packages.contracts.event_bus.bodies.base import EventBody, JsonObject

_MODULE_BY_NAME = {
    "AlertDispatchedBody": "domains.alert.events",
    "AlertRejectedBody": "domains.alert.events",
    "AlertRequestedBody": "domains.alert.events",
    "AiMessageFailedBody": "domains.ai.events",
    "AiMessageReceivedBody": "domains.ai.events",
    "AiMessageRespondedBody": "domains.ai.events",
    "AgentConnectedBody": "domains.target.events",
    "ApprovalGrantedBody": "domains.gitops.events",
    "ApprovalRecommendedBody": "domains.rca.events",
    "ApprovalRejectedBody": "domains.gitops.events",
    "ApprovalRequestedBody": "domains.gitops.events",
    "CauseCandidate": "domains.rca.events",
    "CauseEvaluation": "domains.rca.events",
    "ClusterDesiredStateChangedBody": "domains.target.events",
    "ClusterDriftDetectedBody": "domains.target.events",
    "ClusterEvidenceReceivedBody": "domains.rca.events",
    "ClusterReconcileCompletedBody": "domains.target.events",
    "ClusterReconcileFailedBody": "domains.target.events",
    "ClusterReconcileRequestedBody": "domains.target.events",
    "ClusterReconcileStartedBody": "domains.target.events",
    "CommandCancelRequestedBody": "domains.command.events",
    "CommandCompletedBody": "domains.command.events",
    "CommandDispatchedBody": "domains.command.events",
    "CommandQueuedForAgentBody": "domains.command.events",
    "CommandRejectedBody": "domains.command.events",
    "CommandRequestedBody": "domains.command.events",
    "CommandRetryRequestedBody": "domains.command.events",
    "DeadLetterCreatedBody": "packages.contracts.event_bus.bodies.platform",
    "Diff": "domains.gitops.events",
    "DiffAnalyzedBody": "domains.gitops.events",
    "DesiredDesiredDiffDetectedBody": "domains.gitops.events",
    "DiffExplainedBody": "domains.rca.events",
    "EvidenceReference": "domains.rca.events",
    "EmailVerificationFailedBody": "domains.mail.events",
    "EmailVerificationRequestedBody": "domains.mail.events",
    "EmailVerificationSentBody": "domains.mail.events",
    "Evidence": "domains.rca.events",
    "EvidenceBuiltBody": "domains.rca.events",
    "EvidenceBundle": "domains.rca.events",
    "EvidenceBundleBuiltBody": "domains.rca.events",
    "EvidenceItem": "domains.rca.events",
    "GitChangedBody": "domains.gitops.events",
    "GitOpsChangeContextDetectedBody": "domains.gitops.events",
    "GitWebhookReceivedBody": "domains.gitops.events",
    "HealingActionDraft": "domains.rca.events",
    "IncidentDetectedBody": "domains.rca.events",
    "IncidentRecord": "domains.rca.events",
    "LeaseMetadata": "domains.command.events",
    "Manifest": "domains.gitops.events",
    "ManifestInvalidBody": "domains.gitops.events",
    "ManifestRenderedBody": "domains.gitops.events",
    "Plan": "domains.command.events",
    "PipelineContractFailedBody": "packages.contracts.event_bus.bodies.platform",
    "MissingEvidenceCheck": "domains.rca.events",
    "RcaActionRequiredBody": "domains.rca.events",
    "RcaAnalysisBlockedBody": "domains.rca.events",
    "RcaAiFallbackRequestedBody": "domains.rca.events",
    "RcaBacklogItemCreatedBody": "domains.rca.events",
    "RcaCandidatesEvaluatedBody": "domains.rca.events",
    "RcaCandidatesPlannedBody": "domains.rca.events",
    "RcaCompletedBody": "domains.rca.events",
    "RcaFollowupRequiredBody": "domains.rca.events",
    "RcaReportDetail": "domains.rca.events",
    "RcaRuleMissing": "domains.rca.events",
    "RcaRuleMissingBody": "domains.rca.events",
    "RecoveryActionCandidate": "domains.rca.events",
    "RecoveryActionSelectedBody": "domains.rca.events",
    "RecoveryPlan": "domains.rca.events",
    "RecoveryPlannedBody": "domains.rca.events",
    "RecoveryPrMergedBody": "domains.rca.events",
    "RecoveryPrTrackedBody": "domains.rca.events",
    "RecoveryRetryRequestedBody": "domains.rca.events",
    "RecoverySelectionRequestedBody": "domains.rca.events",
    "RecoveryVerificationFailedBody": "domains.rca.events",
    "RecoveryVerificationStartedBody": "domains.rca.events",
    "RecoveryVerificationUpdatedBody": "domains.rca.events",
    "IncidentResolvedBody": "domains.rca.events",
    "RenderedManifest": "domains.gitops.events",
    "RenderedMetadata": "domains.gitops.events",
    "RenderedSpec": "domains.gitops.events",
    "RetryPolicy": "domains.command.events",
    "RolloutDiagnosedBody": "domains.rca.events",
    "Route": "domains.command.events",
    "RoutingConstraint": "domains.command.events",
    "SafePrCreatedBody": "domains.scm.events",
    "SafePrFailedBody": "domains.scm.events",
    "SafePrFilePatch": "domains.scm.events",
    "SafePrPatchPreparedBody": "domains.rca.events",
    "SafePrReadyForCreationBody": "domains.scm.events",
    "SafePrRequestedBody": "domains.scm.events",
    "TargetDesiredComponent": "domains.target.events",
    "TargetDrift": "domains.target.events",
    "WorkflowCreatedBody": "domains.gitops.events",
    "WorkflowRunCompletedBody": "domains.gitops.events",
    "WorkflowRunFailedBody": "domains.gitops.events",
    "WorkflowRunStartedBody": "domains.gitops.events",
    "WorkflowStepRecordedBody": "domains.gitops.events",
}

__all__ = ["EventBody", "JsonObject", *_MODULE_BY_NAME]


def __getattr__(name: str) -> Any:
    module_name = _MODULE_BY_NAME.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
