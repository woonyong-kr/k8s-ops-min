"""Projection helpers for release-flow run status updates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from domains.alert.events import AlertRequestedBody
from domains.target.evidence_policy import default_evidence_provider_policy
from packages.config.constants import Target
from packages.contracts.event_bus.interfaces import EventEnvelope, JsonObject
from packages.contracts.event_bus.subjects import EventSubject
from packages.contracts.gitops import DEFAULT_ENVIRONMENT
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.contracts.target import TARGET_NAMESPACE

RELEASE_WORKFLOW_SUBJECTS = {
    EventSubject.WORKFLOW_RUN_STARTED.value,
    EventSubject.WORKFLOW_STEP_RECORDED.value,
    EventSubject.APPROVAL_REQUESTED.value,
    EventSubject.APPROVAL_GRANTED.value,
    EventSubject.APPROVAL_REJECTED.value,
    EventSubject.WORKFLOW_RUN_COMPLETED.value,
    EventSubject.WORKFLOW_RUN_FAILED.value,
    EventSubject.EVIDENCE_JOB_UPDATED.value,
    EventSubject.CLUSTER_EVIDENCE_RECEIVED.value,
    EventSubject.EVIDENCE_BUILT.value,
    EventSubject.INCIDENT_DETECTED.value,
    EventSubject.EVIDENCE_BUNDLE_BUILT.value,
    EventSubject.RCA_CANDIDATES_PLANNED.value,
    EventSubject.RCA_CANDIDATES_EVALUATED.value,
    EventSubject.RCA_COMPLETED.value,
    EventSubject.RCA_ANALYSIS_BLOCKED.value,
    EventSubject.RCA_ACTION_REQUIRED.value,
    EventSubject.RECOVERY_PLANNED.value,
    EventSubject.RECOVERY_SELECTION_REQUESTED.value,
    EventSubject.RECOVERY_ACTION_SELECTED.value,
    EventSubject.SAFE_PR_PATCH_PREPARED.value,
    EventSubject.SAFE_PR_REQUESTED.value,
    EventSubject.SAFE_PR_READY_FOR_CREATION.value,
    EventSubject.SAFE_PR_CREATED.value,
    EventSubject.SAFE_PR_FAILED.value,
    EventSubject.COMMAND_REQUESTED.value,
    EventSubject.COMMAND_DISPATCHED.value,
    EventSubject.COMMAND_QUEUED_FOR_AGENT.value,
    EventSubject.COMMAND_REJECTED.value,
    EventSubject.COMMAND_COMPLETED.value,
}
DEFAULT_RELEASE_FAILURE_EVIDENCE_PROVIDERS = ["kubernetes", "metrics", "logs", "traces"]
RELEASE_WORKFLOW_FAILURE_SOURCE_ID = "release-workflow-failure"
RELEASE_VERIFICATION_SOURCE_ID = "post-deploy-verification"
RELEASE_ALERT_EVENT_TYPES = {
    EventSubject.WORKFLOW_RUN_FAILED.value: ("critical", "release workflow failed"),
    EventSubject.APPROVAL_REQUESTED.value: ("warning", "release approval requested"),
    EventSubject.APPROVAL_REJECTED.value: ("critical", "release approval rejected"),
}


def release_workflow_update_from_event(evt: EventEnvelope) -> JsonObject | None:
    subject = str(evt.subject)
    if subject not in RELEASE_WORKFLOW_SUBJECTS:
        return None
    payload = evt.payload if isinstance(evt.payload, dict) else {}
    workflow_run_id = _workflow_run_id(payload)
    if not workflow_run_id:
        return None

    update: JsonObject = {
        "workspace_id": _workspace_id(payload),
        "workflow_run_id": workflow_run_id,
        "application_id": _first_string(
            payload,
            ("application_id",),
            ("release_context", "application_id"),
            ("evidence", "application_id"),
        ),
        "event_type": event_type_for_subject(subject),
        "message": message_for_subject(subject, payload),
        "details": details_for_subject(evt, payload),
    }
    step_status = step_status_for_subject(subject, payload)
    health_status = health_status_for_step_status(step_status)
    verification_health_status = release_verification_health_status(subject, payload)
    approval_id = _first_string(payload, ("approval_id",))
    cluster_id = _first_string(payload, ("cluster_id",), ("evidence", "cluster_id"))
    if step_status:
        update["step_status"] = step_status
    if health_status:
        update["health_status"] = health_status
    if verification_health_status:
        update["health_status"] = verification_health_status
    if approval_id:
        update["approval_id"] = approval_id
    if cluster_id:
        update["cluster_id"] = cluster_id
    return update


def release_failure_evidence_request(
    update: Mapping[str, Any],
    projected: Mapping[str, Any] | None = None,
) -> JsonObject | None:
    if str(update.get("step_status") or "") != "failed":
        return None
    workflow_run_id = str(update.get("workflow_run_id") or "")
    workspace_id = str(update.get("workspace_id") or DEFAULT_WORKSPACE_ID)
    cluster_id = release_failure_cluster_id(update, projected, workflow_run_id)
    if not workflow_run_id or not cluster_id:
        return None
    provider_keys = list(DEFAULT_RELEASE_FAILURE_EVIDENCE_PROVIDERS)
    namespace = release_failure_namespace(update, projected, workflow_run_id)
    release_context = release_failure_context(
        update,
        projected,
        workflow_run_id,
        cluster_id=cluster_id,
        namespace=namespace,
        workspace_id=workspace_id,
    )
    return {
        "workspace_id": workspace_id,
        "cluster_id": cluster_id,
        "source_id": RELEASE_WORKFLOW_FAILURE_SOURCE_ID,
        "window_start": workflow_run_id,
        "provider_keys": provider_keys,
        "failure_policy": "allow_partial",
        "max_attempts": 3,
        "policy_generation": 1,
        "provider_policies": release_failure_provider_policies(
            provider_keys,
            namespace,
            cluster_id,
            release_context,
        ),
    }


def evidence_queued_update(
    update: Mapping[str, Any],
    queued: Mapping[str, Any],
) -> JsonObject:
    return {
        "workspace_id": str(update.get("workspace_id") or DEFAULT_WORKSPACE_ID),
        "workflow_run_id": str(update.get("workflow_run_id") or ""),
        "application_id": str(update.get("application_id") or ""),
        "event_type": "evidence.queued",
        "message": "RCA evidence jobs queued for failed workflow.",
        "details": {"evidence": dict(queued)},
    }


def release_alert_request(
    update: Mapping[str, Any],
    projected: Mapping[str, Any] | None = None,
) -> AlertRequestedBody | None:
    verification_alert = release_verification_alert_request(update, projected)
    if verification_alert is not None:
        return verification_alert
    event_type = str(update.get("event_type") or "")
    if event_type not in RELEASE_ALERT_EVENT_TYPES:
        return None
    workflow_run_id = str(update.get("workflow_run_id") or "")
    workspace_id = str(update.get("workspace_id") or DEFAULT_WORKSPACE_ID)
    severity, reason = RELEASE_ALERT_EVENT_TYPES[event_type]
    cluster_id = (
        release_failure_cluster_id(update, projected, workflow_run_id) or Target.DEFAULT_CLUSTER_ID
    )
    namespace = release_failure_namespace(update, projected, workflow_run_id)
    context = release_failure_context(
        update,
        projected,
        workflow_run_id,
        cluster_id=cluster_id,
        namespace=namespace,
        workspace_id=workspace_id,
    )
    application_id = str(context.get("application_id") or "release")
    environment = str(context.get("environment") or DEFAULT_ENVIRONMENT)
    message = str(update.get("message") or reason)
    return AlertRequestedBody(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=namespace,
        severity=severity,
        application_id=application_id,
        workflow_run_id=workflow_run_id,
        environment=environment,
        message=f"{application_id}: {message}",
        reason=reason,
    )


def release_verification_alert_request(
    update: Mapping[str, Any],
    projected: Mapping[str, Any] | None = None,
) -> AlertRequestedBody | None:
    if str(update.get("event_type") or "") != EventSubject.EVIDENCE_JOB_UPDATED.value:
        return None
    details = mapping_value(update.get("details"))
    release_guard = mapping_value(details.get("release_guard"))
    verification_jobs = mapping_value(release_guard.get("verification_jobs"))
    jobs = [item for item in list_value(verification_jobs.get("jobs")) if isinstance(item, Mapping)]
    failed_jobs = [
        job
        for job in jobs
        if str(job.get("status") or "").lower() in {"failed", "error", "timeout", "unhealthy"}
    ]
    if not failed_jobs:
        return None
    workflow_run_id = str(update.get("workflow_run_id") or "")
    workspace_id = str(update.get("workspace_id") or DEFAULT_WORKSPACE_ID)
    cluster_id = (
        release_failure_cluster_id(update, projected, workflow_run_id) or Target.DEFAULT_CLUSTER_ID
    )
    namespace = release_failure_namespace(update, projected, workflow_run_id)
    context = release_failure_context(
        update,
        projected,
        workflow_run_id,
        cluster_id=cluster_id,
        namespace=namespace,
        workspace_id=workspace_id,
    )
    first_job = failed_jobs[0]
    application_id = str(
        first_job.get("application_id")
        or context.get("application_id")
        or update.get("application_id")
        or "release"
    )
    kind = str(first_job.get("kind") or "verification")
    target = release_verification_alert_target(first_job)
    target_suffix = f" ({target})" if target else ""
    status_label = release_verification_alert_status_label(first_job)
    return AlertRequestedBody(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=namespace,
        severity="critical",
        application_id=application_id,
        workflow_run_id=workflow_run_id,
        environment=str(context.get("environment") or DEFAULT_ENVIRONMENT),
        message=f"{application_id}: post-deploy verification {kind} {status_label}{target_suffix}",
        reason="release verification failed",
    )


def release_verification_alert_status_label(job: Mapping[str, Any]) -> str:
    status = str(job.get("status") or "").strip().lower()
    return "timed out" if status == "timeout" else "failed"


def release_verification_alert_target(job: Mapping[str, Any]) -> str:
    target = mapping_value(job.get("target"))
    return _first_string(target, ("url",), ("path",), ("service_name",))


def release_failure_cluster_id(
    update: Mapping[str, Any],
    projected: Mapping[str, Any] | None,
    workflow_run_id: str,
) -> str:
    explicit = str(update.get("cluster_id") or "")
    if explicit:
        return explicit
    step = release_step_for_workflow(projected, workflow_run_id)
    details = mapping_value(step.get("details"))
    config = mapping_value(details.get("config"))
    return _first_string(details, ("cluster_id",)) or _first_string(config, ("cluster_id",))


def release_failure_namespace(
    update: Mapping[str, Any],
    projected: Mapping[str, Any] | None,
    workflow_run_id: str,
) -> str:
    update_details = mapping_value(update.get("details"))
    workflow_details = mapping_value(update_details.get("workflow_details"))
    namespace = _first_string(workflow_details, ("namespace",))
    if namespace:
        return namespace
    step = release_step_for_workflow(projected, workflow_run_id)
    details = mapping_value(step.get("details"))
    config = mapping_value(details.get("config"))
    return (
        _first_string(details, ("namespace",))
        or _first_string(config, ("namespace",))
        or TARGET_NAMESPACE
    )


def release_failure_context(
    update: Mapping[str, Any],
    projected: Mapping[str, Any] | None,
    workflow_run_id: str,
    *,
    cluster_id: str,
    namespace: str,
    workspace_id: str,
) -> JsonObject:
    step = release_step_for_workflow(projected, workflow_run_id)
    step_details = mapping_value(step.get("details"))
    step_workflow_details = mapping_value(step_details.get("workflow_details"))
    config = mapping_value(step_details.get("config"))
    update_details = mapping_value(update.get("details"))
    workflow_details = mapping_value(update_details.get("workflow_details"))
    context: JsonObject = {
        "workspace_id": workspace_id,
        "workflow_run_id": workflow_run_id,
        "application_id": _first_string(update, ("application_id",))
        or _first_string(step, ("application_id",)),
        "cluster_id": cluster_id,
        "namespace": namespace,
    }
    for key in (
        "environment",
        "manifest_path",
        "repo_ref",
        "branch",
        "commit_sha",
        "image",
        "replicas",
        "resource_kind",
        "resource_name",
        "runtime_mode",
        "provider_mode",
    ):
        value = (
            _first_value(workflow_details, (key,))
            or _first_value(step_workflow_details, (key,))
            or _first_value(step_details, (key,))
            or _first_value(config, (key,))
        )
        if value not in (None, ""):
            context[key] = value
    for key in ("desired_manifest", "rendered_manifest"):
        value = (
            _first_value(workflow_details, (key,))
            or _first_value(workflow_details, ("diff", key))
            or _first_value(step_workflow_details, (key,))
            or _first_value(step_workflow_details, ("diff", key))
            or _first_value(step_details, (key,))
            or _first_value(config, (key,))
        )
        if isinstance(value, Mapping) and value:
            context[key] = dict(value)
    rendered = rendered_manifest_details(workflow_details) or rendered_manifest_details(
        step_workflow_details
    )
    if rendered:
        context.setdefault("rendered_manifest", rendered)
        manifest = mapping_value(rendered.get("manifest"))
        if manifest:
            context.setdefault("desired_manifest", dict(manifest))
    context.update(resource_hint_from_context(context))
    return {key: value for key, value in context.items() if value not in (None, "")}


def rendered_manifest_details(details: Mapping[str, Any]) -> JsonObject:
    if not details:
        return {}
    if isinstance(details.get("rendered_manifest"), Mapping):
        return dict(details["rendered_manifest"])
    if isinstance(details.get("manifest"), Mapping) and details.get("kind"):
        return dict(details)
    return {}


def release_step_for_workflow(
    projected: Mapping[str, Any] | None,
    workflow_run_id: str,
) -> Mapping[str, Any]:
    for step in list_value(mapping_value(projected).get("steps")):
        if not isinstance(step, Mapping):
            continue
        if str(step.get("workflow_run_id") or "") == workflow_run_id:
            return step
    return {}


def release_failure_provider_policies(
    provider_keys: list[str],
    namespace: str,
    cluster_id: str,
    release_context: Mapping[str, Any] | None = None,
) -> JsonObject:
    interval_seconds = int(Target.DEFAULT_EVIDENCE_INTERVAL_SECONDS)
    return {
        provider_key: release_failure_provider_policy(
            provider_key,
            namespace,
            interval_seconds,
            cluster_id,
            release_context,
        )
        for provider_key in provider_keys
    }


def release_failure_provider_policy(
    provider_key: str,
    namespace: str,
    interval_seconds: int,
    cluster_id: str,
    release_context: Mapping[str, Any] | None = None,
) -> JsonObject:
    policy = default_evidence_provider_policy(
        provider_key,
        interval_seconds,
        cluster_id=cluster_id,
    ).model_dump()
    queries = policy.get("queries")
    if isinstance(queries, list):
        policy["queries"] = [
            release_failure_query_for_namespace(provider_key, query, namespace, release_context)
            for query in queries
            if isinstance(query, Mapping)
        ]
    if release_context:
        policy["release_context"] = dict(release_context)
    return policy


def release_failure_query_for_namespace(
    provider_key: str,
    query: Mapping[str, Any],
    namespace: str,
    release_context: Mapping[str, Any] | None = None,
) -> JsonObject:
    payload = dict(query)
    provenance = mapping_value(payload.get("provenance"))
    if provenance.get("query_scope") == "namespace":
        old_namespaces = [str(item) for item in list_value(provenance.get("namespaces"))]
        old_matchers = [str(item) for item in list_value(provenance.get("required_matchers"))]
        provenance["namespaces"] = [namespace]
        provenance["required_matchers"] = [
            matcher.replace('namespace="target"', f'namespace="{namespace}"').replace(
                'k8s_namespace_name="target"',
                f'k8s_namespace_name="{namespace}"',
            )
            for matcher in old_matchers
        ]
        if not provenance["required_matchers"] and "target" in old_namespaces:
            provenance["required_matchers"] = [namespace]
        payload["provenance"] = provenance
    if provider_key == "kubernetes":
        if provenance.get("query_scope") == "cluster":
            return payload
        payload["query"] = namespace
        for key in ("resource_kind", "resource_name", "label_selector"):
            value = mapping_value(release_context).get(key)
            if value not in (None, ""):
                payload[key] = value
        return payload
    raw_query = str(payload.get("query") or "")
    if raw_query:
        payload["query"] = raw_query.replace('namespace="target"', f'namespace="{namespace}"')
        payload["query"] = payload["query"].replace(
            'k8s_namespace_name="target"',
            f'k8s_namespace_name="{namespace}"',
        )
    return payload


def resource_hint_from_context(context: Mapping[str, Any]) -> JsonObject:
    hint: JsonObject = {}
    resource_kind = _first_string(context, ("resource_kind",))
    resource_name = _first_string(context, ("resource_name",))
    manifest = mapping_value(context.get("desired_manifest")) or mapping_value(
        context.get("rendered_manifest")
    )
    metadata = mapping_value(manifest.get("metadata"))
    if not resource_kind:
        resource_kind = _first_string(manifest, ("kind",))
    if not resource_name:
        resource_name = _first_string(metadata, ("name",))
    selector = label_selector_from_manifest(manifest)
    if resource_kind:
        hint["resource_kind"] = resource_kind
    if resource_name:
        hint["resource_name"] = resource_name
    if selector:
        hint["label_selector"] = selector
    return hint


def label_selector_from_manifest(manifest: Mapping[str, Any]) -> str:
    selector = mapping_value(_first_value(manifest, ("spec", "selector", "matchLabels")))
    if not selector:
        return ""
    return ",".join(f"{key}={value}" for key, value in sorted(selector.items()))


def step_status_for_subject(subject: str, payload: Mapping[str, Any]) -> str | None:
    if subject == EventSubject.WORKFLOW_RUN_STARTED.value:
        return "running"
    if subject == EventSubject.WORKFLOW_STEP_RECORDED.value:
        status = str(payload.get("status") or "")
        if status == "failed":
            return "failed"
        return "running"
    if subject == EventSubject.APPROVAL_REQUESTED.value:
        return "waiting_for_approval"
    if subject == EventSubject.APPROVAL_GRANTED.value:
        return "running"
    if subject == EventSubject.APPROVAL_REJECTED.value:
        return "failed"
    if subject == EventSubject.WORKFLOW_RUN_COMPLETED.value:
        return "succeeded"
    if subject == EventSubject.WORKFLOW_RUN_FAILED.value:
        return "failed"
    return None


def health_status_for_step_status(step_status: str | None) -> str | None:
    if step_status == "succeeded":
        return "healthy"
    if step_status == "failed":
        return "unhealthy"
    if step_status in {"running", "waiting_for_approval"}:
        return "progressing"
    return None


def event_type_for_subject(subject: str) -> str:
    if subject.startswith("workflow."):
        return subject.replace("workflow.", "workflow.", 1)
    if subject.startswith("approval."):
        return subject
    if subject == EventSubject.CLUSTER_EVIDENCE_RECEIVED.value:
        return "evidence.received"
    if subject.startswith("evidence."):
        return subject
    if subject.startswith("incident."):
        return subject
    if subject.startswith("rca."):
        return subject
    if subject.startswith("recovery."):
        return subject
    if subject.startswith("safe_pr."):
        return subject
    if subject.startswith("command."):
        return subject
    return subject


def message_for_subject(subject: str, payload: Mapping[str, Any]) -> str:
    if subject == EventSubject.WORKFLOW_RUN_STARTED.value:
        return "Workflow run started."
    if subject == EventSubject.WORKFLOW_STEP_RECORDED.value:
        step = str(payload.get("step") or "workflow")
        status = str(payload.get("status") or "recorded")
        message = str(payload.get("message") or "")
        suffix = f": {message}" if message else ""
        return f"Workflow step {step} {status}{suffix}."
    if subject == EventSubject.APPROVAL_REQUESTED.value:
        return str(payload.get("reason") or "Workflow approval requested.")
    if subject == EventSubject.APPROVAL_GRANTED.value:
        return "Workflow approval granted."
    if subject == EventSubject.APPROVAL_REJECTED.value:
        return str(payload.get("reason") or "Workflow approval rejected.")
    if subject == EventSubject.WORKFLOW_RUN_COMPLETED.value:
        return str(payload.get("summary") or "Workflow run completed.")
    if subject == EventSubject.WORKFLOW_RUN_FAILED.value:
        return str(payload.get("reason") or "Workflow run failed.")
    if subject == EventSubject.EVIDENCE_JOB_UPDATED.value:
        provider = _first_string(payload, ("provider_key",)) or "provider"
        status = _first_string(payload, ("status",)) or "updated"
        if bool(payload.get("evidence_emitted")):
            return f"Evidence job {provider} {status}; RCA evidence emitted."
        return f"Evidence job {provider} {status}; waiting for remaining evidence."
    if subject == EventSubject.CLUSTER_EVIDENCE_RECEIVED.value:
        return "Cluster evidence received for release workflow."
    if subject == EventSubject.EVIDENCE_BUILT.value:
        return "RCA evidence object built for release workflow."
    if subject == EventSubject.INCIDENT_DETECTED.value:
        if bool(payload.get("detected")):
            return "Incident signal detected from release evidence."
        return str(payload.get("reason") or "No incident signal detected from release evidence.")
    if subject == EventSubject.EVIDENCE_BUNDLE_BUILT.value:
        return "RCA evidence bundle built for release workflow."
    if subject == EventSubject.RCA_CANDIDATES_PLANNED.value:
        return f"RCA candidates planned ({int_like(payload.get('candidate_count'), 0)})."
    if subject == EventSubject.RCA_CANDIDATES_EVALUATED.value:
        return f"RCA candidates evaluated ({int_like(payload.get('candidate_count'), 0)})."
    if subject == EventSubject.RCA_COMPLETED.value:
        return str(payload.get("root_cause") or "RCA completed for release workflow.")
    if subject == EventSubject.RCA_ANALYSIS_BLOCKED.value:
        return str(payload.get("reason") or "RCA analysis blocked for release workflow.")
    if subject == EventSubject.RCA_ACTION_REQUIRED.value:
        return str(payload.get("reason") or "RCA action required for release workflow.")
    if subject == EventSubject.RECOVERY_PLANNED.value:
        return "Recovery options planned from RCA result."
    if subject == EventSubject.RECOVERY_SELECTION_REQUESTED.value:
        return str(payload.get("reason") or "Recovery action selection required.")
    if subject == EventSubject.RECOVERY_ACTION_SELECTED.value:
        return str(payload.get("reason") or "Recovery action selected.")
    if subject == EventSubject.SAFE_PR_PATCH_PREPARED.value:
        return "Safe PR patch prepared from recovery plan."
    if subject == EventSubject.SAFE_PR_REQUESTED.value:
        return str(payload.get("title") or "Safe PR requested.")
    if subject == EventSubject.SAFE_PR_READY_FOR_CREATION.value:
        return str(payload.get("summary") or "Safe PR ready for creation.")
    if subject == EventSubject.SAFE_PR_CREATED.value:
        return str(payload.get("pr_url") or "Safe PR created.")
    if subject == EventSubject.SAFE_PR_FAILED.value:
        return str(payload.get("reason") or "Safe PR creation failed.")
    if subject == EventSubject.COMMAND_REQUESTED.value:
        return str(payload.get("reason") or "Recovery command requested.")
    if subject == EventSubject.COMMAND_DISPATCHED.value:
        return "Recovery command dispatched."
    if subject == EventSubject.COMMAND_QUEUED_FOR_AGENT.value:
        return "Recovery command queued for cluster agent."
    if subject == EventSubject.COMMAND_REJECTED.value:
        return str(payload.get("reason") or "Recovery command rejected.")
    if subject == EventSubject.COMMAND_COMPLETED.value:
        result = mapping_value(payload.get("result"))
        return str(result.get("message") or result.get("status") or "Recovery command completed.")
    return subject


def details_for_subject(evt: EventEnvelope, payload: Mapping[str, Any]) -> JsonObject:
    workflow = {
        "workflow_run_id": _workflow_run_id(payload),
        "event_id": evt.event_id,
        "subject": str(evt.subject),
        "correlation_id": evt.correlation_id,
        "created_at": evt.created_at,
    }
    step = _first_string(payload, ("step",), ("current_step",))
    status = _first_string(payload, ("status",))
    if step:
        workflow["current_step"] = step
    if status:
        workflow["status"] = status
    details = dict(mapping_value(payload.get("details")))
    return {
        "workflow_projection": workflow,
        "workflow_details": details,
        **subject_details(str(evt.subject), payload),
    }


def subject_details(subject: str, payload: Mapping[str, Any]) -> JsonObject:
    if subject == EventSubject.APPROVAL_REQUESTED.value:
        return {
            "approval": {
                "approval_id": _first_string(payload, ("approval_id",)),
                "reason": _first_string(payload, ("reason",)),
                "requested_role": _first_string(payload, ("requested_role",)),
            }
        }
    if subject in {EventSubject.APPROVAL_GRANTED.value, EventSubject.APPROVAL_REJECTED.value}:
        return {
            "approval": {
                "approval_id": _first_string(payload, ("approval_id",)),
                "decision": _first_string(payload, ("decision",)),
                "decided_by": _first_string(payload, ("decided_by",)),
                "reason": _first_string(payload, ("reason",)),
            }
        }
    if subject == EventSubject.CLUSTER_EVIDENCE_RECEIVED.value:
        return {
            "evidence": evidence_details(payload),
        }
    if subject == EventSubject.EVIDENCE_JOB_UPDATED.value:
        details = {
            "evidence": evidence_details(payload),
            "evidence_job": evidence_job_details(payload),
        }
        verification_update = release_verification_job_projection(payload)
        if verification_update:
            details["release_guard"] = {"verification_jobs": {"jobs": [verification_update]}}
        return details
    if subject == EventSubject.EVIDENCE_BUILT.value:
        return {"evidence": evidence_details(payload)}
    if subject == EventSubject.INCIDENT_DETECTED.value:
        return {
            "evidence": evidence_details(payload),
            "incident": incident_details(payload),
        }
    if subject == EventSubject.EVIDENCE_BUNDLE_BUILT.value:
        return {
            "evidence": evidence_details(payload),
            "incident": incident_details(payload),
            "evidence_bundle": evidence_bundle_details(payload),
        }
    if subject in {
        EventSubject.RCA_CANDIDATES_PLANNED.value,
        EventSubject.RCA_CANDIDATES_EVALUATED.value,
    }:
        return {
            "evidence": evidence_details(payload),
            "incident": incident_details(payload),
            "evidence_bundle": evidence_bundle_details(payload),
            "rca": rca_candidate_details(payload),
        }
    if subject == EventSubject.RCA_COMPLETED.value:
        return {
            "evidence": evidence_details(payload),
            "incident": incident_details(payload),
            "evidence_bundle": evidence_bundle_details(payload),
            "rca": {
                "root_cause": _first_string(payload, ("root_cause",), ("rca_detail", "root_cause")),
                "action": _first_string(payload, ("action",)),
                "evidence_ref": _first_string(payload, ("evidence_ref",)),
                "confidence": _first_value(payload, ("rca_detail", "confidence")),
            },
        }
    if subject in {
        EventSubject.RCA_ANALYSIS_BLOCKED.value,
        EventSubject.RCA_ACTION_REQUIRED.value,
    }:
        return {
            "evidence": evidence_details(payload),
            "incident": incident_details(payload),
            "evidence_bundle": evidence_bundle_details(payload),
            "rca": {
                "reason_code": _first_string(payload, ("reason_code",)),
                "reason": _first_string(payload, ("reason",)),
                "evidence_ref": _first_string(payload, ("evidence_ref",)),
                "missing_evidence": _first_value(payload, ("missing_evidence",)) or [],
            },
        }
    if subject in {
        EventSubject.RECOVERY_PLANNED.value,
        EventSubject.RECOVERY_SELECTION_REQUESTED.value,
        EventSubject.RECOVERY_ACTION_SELECTED.value,
    }:
        return {"recovery": recovery_details(payload)}
    if subject in {
        EventSubject.SAFE_PR_PATCH_PREPARED.value,
        EventSubject.SAFE_PR_REQUESTED.value,
        EventSubject.SAFE_PR_READY_FOR_CREATION.value,
        EventSubject.SAFE_PR_CREATED.value,
        EventSubject.SAFE_PR_FAILED.value,
    }:
        return {"safe_pr": safe_pr_details(payload)}
    if subject in {
        EventSubject.COMMAND_REQUESTED.value,
        EventSubject.COMMAND_DISPATCHED.value,
        EventSubject.COMMAND_QUEUED_FOR_AGENT.value,
        EventSubject.COMMAND_REJECTED.value,
        EventSubject.COMMAND_COMPLETED.value,
    }:
        return {"command": command_details(payload)}
    return {}


def evidence_details(payload: Mapping[str, Any]) -> JsonObject:
    evidence = mapping_value(payload.get("evidence"))
    collection_status = collection_status_details(payload, evidence)
    failed_providers = list_value(collection_status.get("failed_providers"))
    completed_providers = list_value(collection_status.get("completed_providers"))
    pending_providers = list_value(collection_status.get("pending_providers"))
    return {
        "evidence_key": _first_string(payload, ("evidence_key",), ("evidence", "evidence_key")),
        "source_id": _first_string(payload, ("source_id",), ("evidence", "source_id")),
        "agent_id": _first_string(payload, ("agent_id",), ("evidence", "agent_id")),
        "window_start": _first_string(payload, ("window_start",), ("evidence", "window_start")),
        "workflow_run_id": _workflow_run_id(payload),
        "object_ref": _first_string(payload, ("evidence", "object_ref")),
        "cluster_id": _first_string(payload, ("cluster_id",), ("evidence", "cluster_id")),
        "has_kubernetes": evidence_source_present(
            "kubernetes",
            payload.get("kubernetes") or evidence.get("kubernetes"),
            collection_status,
        ),
        "has_metrics": evidence_source_present(
            "metrics",
            payload.get("metrics") or evidence.get("metrics"),
            collection_status,
        ),
        "has_logs": evidence_source_present(
            "logs",
            payload.get("logs") or evidence.get("logs"),
            collection_status,
        ),
        "has_traces": evidence_source_present(
            "traces",
            payload.get("traces") or evidence.get("traces"),
            collection_status,
        ),
        "collection_complete": collection_status.get("complete"),
        "failed_providers": failed_providers,
        "failed_provider_count": len(failed_providers),
        "completed_providers": completed_providers,
        "completed_provider_count": len(completed_providers),
        "pending_providers": pending_providers,
        "pending_provider_count": len(pending_providers),
        "collection_status": collection_status,
    }


def collection_status_details(
    payload: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> JsonObject:
    direct = mapping_value(payload.get("collection_status"))
    nested = mapping_value(evidence.get("collection_status"))
    evidence_metadata = mapping_value(evidence.get("metadata"))
    metadata_status = mapping_value(evidence_metadata.get("collection_status"))
    status = direct or nested or metadata_status
    return dict(status) if status else {}


def evidence_source_present(
    source: str,
    value: object,
    collection_status: Mapping[str, Any],
) -> bool:
    """Avoid presenting unavailable telemetry envelopes as collected evidence."""
    providers = mapping_value(collection_status.get("providers"))
    provider_status = mapping_value(providers.get(source))
    if provider_status.get("status") in {"unavailable", "not_queried"}:
        return False
    if source == "logs":
        return isinstance(value, list) and bool(value)
    if not isinstance(value, Mapping):
        return False
    if source == "metrics":
        alertmanager = value.get("alertmanager")
        if isinstance(alertmanager, Mapping) and bool(alertmanager):
            return True
        if value.get("source") == "prometheus" or "results" in value:
            results = value.get("results")
            return isinstance(results, Mapping) and bool(results)
    if source == "traces" and (value.get("source") == "tempo" or "results" in value):
        results = value.get("results")
        return isinstance(results, Mapping) and bool(results)
    return any(
        item not in (None, "", [], {})
        for key, item in value.items()
        if key != "_lineage"
    )


def evidence_job_details(payload: Mapping[str, Any]) -> JsonObject:
    return {
        "job_id": _first_string(payload, ("job_id",)),
        "provider_key": _first_string(payload, ("provider_key",)),
        "status": _first_string(payload, ("status",)),
        "reported_status": _first_string(payload, ("reported_status",)),
        "error": _first_string(payload, ("error",)),
        "evidence_emitted": bool(payload.get("evidence_emitted")),
        "emitted_event_id": _first_string(payload, ("emitted_event_id",)),
        "emitted_correlation_id": _first_string(payload, ("emitted_correlation_id",)),
    }


def release_verification_job_projection(payload: Mapping[str, Any]) -> JsonObject:
    if not is_release_verification_update(payload):
        return {}
    status = _first_string(payload, ("status",), ("reported_status",)) or "updated"
    result = mapping_value(payload.get("result")) or mapping_value(payload.get("collection_status"))
    error = _first_string(payload, ("error",), ("reason",))
    update: JsonObject = {
        "job_id": _first_string(payload, ("job_id",)),
        "kind": _first_string(payload, ("kind",), ("provider_key",)),
        "status": status,
        "reported_at": _first_string(payload, ("reported_at",), ("updated_at",)),
        "evidence_key": _first_string(payload, ("evidence_key",), ("evidence", "evidence_key")),
        "workflow_run_id": _workflow_run_id(payload),
    }
    if result:
        update["result"] = dict(result)
    if error:
        update["error"] = error
    return {key: value for key, value in update.items() if value not in (None, "", {})}


def is_release_verification_update(payload: Mapping[str, Any]) -> bool:
    source_id = _source_id(payload)
    evidence_key = _first_string(payload, ("evidence_key",), ("evidence", "evidence_key"))
    job_id = _first_string(payload, ("job_id",))
    return (
        source_id == RELEASE_VERIFICATION_SOURCE_ID
        or evidence_key.endswith(RELEASE_VERIFICATION_SOURCE_ID)
        or f":{RELEASE_VERIFICATION_SOURCE_ID}" in evidence_key
        or job_id.startswith("release-verification-")
    )


def release_verification_health_status(subject: str, payload: Mapping[str, Any]) -> str | None:
    if subject != EventSubject.EVIDENCE_JOB_UPDATED.value or not is_release_verification_update(
        payload
    ):
        return None
    status = _first_string(payload, ("status",), ("reported_status",)).lower()
    if status in {"failed", "error", "timeout", "unhealthy"}:
        return "unhealthy"
    if status in {"completed", "succeeded", "passed", "healthy"}:
        return "healthy"
    return "progressing" if status in {"running", "pending", "queued"} else None


def incident_details(payload: Mapping[str, Any]) -> JsonObject:
    incident = mapping_value(payload.get("incident"))
    affected = payload.get("affected")
    return {
        "incident_id": _first_string(payload, ("incident", "incident_id")),
        "detected": _first_value(payload, ("detected",)),
        "reason": _first_string(payload, ("reason",)),
        "severity": _first_string(payload, ("severity",), ("incident", "severity")),
        "symptom": _first_string(payload, ("incident", "symptom")),
        "summary": _first_string(payload, ("incident", "summary")),
        "resource_kind": _first_string(payload, ("incident", "resource_kind")),
        "resource_name": _first_string(payload, ("incident", "resource_name")),
        "namespace": _first_string(payload, ("incident", "namespace")),
        "affected_count": len(affected) if isinstance(affected, list) else None,
        **(
            {"workspace_id": str(incident.get("workspace_id"))}
            if incident.get("workspace_id")
            else {}
        ),
    }


def evidence_bundle_details(payload: Mapping[str, Any]) -> JsonObject:
    bundle = mapping_value(payload.get("evidence_bundle"))
    missing = bundle.get("missing_evidence")
    items = bundle.get("items")
    return {
        "incident_id": _first_string(payload, ("evidence_bundle", "incident_id")),
        "complete": _first_value(payload, ("evidence_bundle", "complete")),
        "missing_evidence": missing if isinstance(missing, list) else [],
        "item_count": len(items) if isinstance(items, list) else 0,
    }


def rca_candidate_details(payload: Mapping[str, Any]) -> JsonObject:
    candidates = payload.get("candidates")
    evaluations = payload.get("evaluations")
    return {
        "evidence_ref": _first_string(payload, ("evidence_ref",)),
        "candidate_count": int_like(payload.get("candidate_count"), 0),
        "candidate_ids": [
            str(candidate.get("candidate_id"))
            for candidate in list_value(candidates)
            if isinstance(candidate, Mapping) and candidate.get("candidate_id")
        ],
        "evaluation_count": len(evaluations) if isinstance(evaluations, list) else 0,
    }


def recovery_details(payload: Mapping[str, Any]) -> JsonObject:
    plan = mapping_value(payload.get("plan"))
    draft = mapping_value(payload.get("draft"))
    selected = mapping_value(payload.get("selected"))
    selected_draft = mapping_value(selected.get("draft"))
    candidates = list_value(plan.get("candidates"))
    return {
        "plan_id": _first_string(payload, ("plan", "plan_id")),
        "evidence_ref": _first_string(payload, ("plan", "evidence_ref")),
        "execution_route": _first_string(payload, ("plan", "execution_route")),
        "recommended_action_id": _first_string(payload, ("plan", "recommended_action_id")),
        "selection_required": _first_value(payload, ("plan", "selection_required")),
        "candidate_count": len(candidates),
        "selected_action_id": _first_string(payload, ("selected", "action_id")),
        "selected_title": _first_string(payload, ("selected", "title")),
        "selected_route": _first_string(payload, ("selected", "route")),
        "selected_by": _first_string(payload, ("selected_by",)),
        "auto_selected": _first_value(payload, ("auto_selected",)),
        "reason": _first_string(payload, ("reason",)),
        "action_type": _first_string(draft, ("action_type",))
        or _first_string(selected_draft, ("action_type",)),
        "workflow_run_id": _workflow_run_id(payload),
    }


def safe_pr_details(payload: Mapping[str, Any]) -> JsonObject:
    request = mapping_value(payload.get("request"))
    details = mapping_value(payload.get("details"))
    patches = payload.get("patches")
    if not isinstance(patches, list):
        patches = request.get("patches")
    return {
        "title": _first_string(payload, ("title",), ("request", "title")),
        "provider": _first_string(payload, ("provider",), ("request", "provider")),
        "repo_ref": _first_string(payload, ("repo_ref",), ("request", "repo_ref")),
        "base_branch": _first_string(payload, ("base_branch",), ("request", "base_branch")),
        "manifest_path": _first_string(payload, ("manifest_path",), ("request", "manifest_path")),
        "commit_sha": _first_string(payload, ("commit_sha",), ("request", "commit_sha")),
        "patch_sha256": _first_string(payload, ("patch_sha256",), ("request", "patch_sha256")),
        "pr_url": _first_string(payload, ("pr_url",)),
        "mode": _first_string(payload, ("mode",)),
        "risk": _first_string(payload, ("risk",)),
        "reason": _first_string(payload, ("reason",)),
        "reason_code": _first_string(payload, ("reason_code",)),
        "stage": _first_string(payload, ("stage",)),
        "exception_type": _first_string(details, ("exception_type",)),
        "summary": _first_string(payload, ("summary",)),
        "workflow_run_id": _workflow_run_id(payload),
        "patch_count": len(patches) if isinstance(patches, list) else 0,
        "approval_ref": _first_string(payload, ("approval_ref",), ("request", "approval_ref")),
        "policy_decision_ref": _first_string(
            payload,
            ("policy_decision_ref",),
            ("request", "policy_decision_ref"),
        ),
    }


def command_details(payload: Mapping[str, Any]) -> JsonObject:
    plan = mapping_value(payload.get("plan"))
    route = mapping_value(payload.get("route"))
    result = mapping_value(payload.get("result"))
    return {
        "command_id": _first_string(payload, ("command_id",), ("plan", "command_id")),
        "action": _first_string(payload, ("action",), ("plan", "action"), ("requested", "action")),
        "cluster_id": _first_string(
            payload,
            ("cluster_id",),
            ("plan", "cluster_id"),
            ("route", "cluster_id"),
            ("requested", "cluster_id"),
        ),
        "namespace": _first_string(
            payload,
            ("namespace",),
            ("plan", "namespace"),
            ("requested", "namespace"),
        ),
        "workflow_run_id": _workflow_run_id(payload),
        "approval_ref": _first_string(payload, ("approval_ref",), ("plan", "approval_ref")),
        "policy_decision_ref": _first_string(
            payload,
            ("policy_decision_ref",),
            ("plan", "policy_decision_ref"),
        ),
        "route_channel": str(route.get("channel") or ""),
        "required_capability": _first_string(
            plan,
            ("routing_constraint", "required_capability"),
        ),
        "status": _first_string(result, ("status",)),
        "applied": _first_value(result, ("applied",)),
        "reason": _first_string(payload, ("reason",), ("requested", "reason")),
    }


def _workflow_run_id(payload: Mapping[str, Any]) -> str:
    workflow_run_id = _first_string(
        payload,
        ("workflow_run_id",),
        ("release_context", "workflow_run_id"),
        ("evidence", "workflow_run_id"),
        ("draft", "params", "workflow_run_id"),
        ("plan", "target", "workflow_run_id"),
        ("selected", "draft", "params", "workflow_run_id"),
        ("request", "workflow_run_id"),
        ("plan", "workflow_run_id"),
        ("details", "workflow_run_id"),
        ("result", "workflow_run_id"),
        ("requested", "workflow_run_id"),
        ("requested", "plan", "workflow_run_id"),
        ("requested", "diff", "workflow_run_id"),
        ("selected", "draft", "params", "workflow_run_id"),
    )
    if workflow_run_id:
        return workflow_run_id
    if _source_id(payload) == RELEASE_WORKFLOW_FAILURE_SOURCE_ID:
        return _first_string(payload, ("window_start",), ("evidence", "window_start"))
    if is_release_verification_update(payload):
        return _first_string(payload, ("workflow_run_id",), ("release_context", "workflow_run_id"))
    return ""


def _source_id(payload: Mapping[str, Any]) -> str:
    return _first_string(payload, ("source_id",), ("evidence", "source_id"))


def _workspace_id(payload: Mapping[str, Any]) -> str:
    return (
        _first_string(
            payload,
            ("workspace_id",),
            ("release_context", "workspace_id"),
            ("evidence", "workspace_id"),
            ("draft", "params", "workspace_id"),
            ("plan", "target", "workspace_id"),
            ("selected", "draft", "params", "workspace_id"),
            ("request", "workspace_id"),
            ("plan", "workspace_id"),
            ("requested", "workspace_id"),
            ("requested", "diff", "workspace_id"),
            ("selected", "draft", "params", "workspace_id"),
        )
        or DEFAULT_WORKSPACE_ID
    )


def _first_string(payload: Mapping[str, Any], *paths: tuple[str, ...]) -> str:
    value = _first_value(payload, *paths)
    return str(value) if value not in (None, "") else ""


def _first_value(payload: Mapping[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = payload
        for part in path:
            if not isinstance(value, Mapping):
                value = None
                break
            value = value.get(part)
        if value not in (None, ""):
            return value
    return None


def mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def int_like(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
