from __future__ import annotations

from packages.contracts.gateway.requests import (
    AgentPolicy,
    EvidenceProviderPolicy,
    EvidenceRuntimePolicy,
)


def merge_agent_policy(base: AgentPolicy, incoming: AgentPolicy) -> AgentPolicy:
    payload = base.model_dump()
    payload["generation"] = incoming.generation
    if "cluster_id" in incoming.model_fields_set:
        payload["cluster_id"] = incoming.cluster_id
    if "cluster_role" in incoming.model_fields_set:
        payload["cluster_role"] = incoming.cluster_role

    if "evidence" in incoming.model_fields_set:
        payload["evidence"] = merge_evidence_policy(
            base.evidence,
            incoming.evidence,
        ).model_dump()
    if "bootstrap" in incoming.model_fields_set:
        payload["bootstrap"] = incoming.bootstrap.model_dump()
    if "desired_state" in incoming.model_fields_set:
        payload["desired_state"] = incoming.desired_state.model_dump()
    if "scheduling" in incoming.model_fields_set:
        payload["scheduling"] = incoming.scheduling.model_dump()
    return AgentPolicy.model_validate(payload)


def merge_evidence_policy(
    base: EvidenceRuntimePolicy,
    incoming: EvidenceRuntimePolicy,
) -> EvidenceRuntimePolicy:
    payload = base.model_dump()
    if "failure_policy" in incoming.model_fields_set:
        payload["failure_policy"] = incoming.failure_policy
    if "max_attempts" in incoming.model_fields_set:
        payload["max_attempts"] = incoming.max_attempts
    providers = dict(payload.get("providers", {}))
    if "providers" in incoming.model_fields_set:
        for provider_key, provider_policy in incoming.providers.items():
            base_provider = base.providers.get(provider_key, EvidenceProviderPolicy())
            providers[provider_key] = merge_provider_policy(
                base_provider,
                provider_policy,
            ).model_dump()
    payload["providers"] = providers
    return EvidenceRuntimePolicy.model_validate(payload)


def merge_provider_policy(
    base: EvidenceProviderPolicy,
    incoming: EvidenceProviderPolicy,
) -> EvidenceProviderPolicy:
    payload = base.model_dump()
    for field_name in incoming.model_fields_set:
        payload[field_name] = getattr(incoming, field_name)
    return EvidenceProviderPolicy.model_validate(payload)
