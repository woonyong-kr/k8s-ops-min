"""Project allow-listed cloud provider facts from stored Kubernetes observations."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from packages.contracts.inventory_provider import (
    AwsAddon,
    AwsMachineProviderDetail,
    AwsManagedClusterProviderDetail,
    AwsManagedControlPlaneProviderDetail,
    AwsManagedMachinePoolProviderDetail,
    AwsSecurityGroup,
    AwsSubnet,
    AzureMachineProviderDetail,
    AzureManagedControlPlaneProviderDetail,
    AzureManagedMachinePoolProviderDetail,
    CapiClusterProviderDetail,
    CapiKubeadmControlPlaneProviderDetail,
    CapiMachineDeploymentProviderDetail,
    CapiMachineHealthCheckProviderDetail,
    CapiMachinePoolProviderDetail,
    CapiMachineProviderDetail,
    CapiMachineSetProviderDetail,
    CapiUnhealthyCondition,
    CertificatePrivateKeyDetail,
    CertificateProviderDetail,
    CertificateRequestProviderDetail,
    ClusterComplianceReportProviderDetail,
    ComplianceControlDetail,
    CronWorkflowProviderDetail,
    CrossplaneCompositeProviderDetail,
    CrossplaneManagedResourceProviderDetail,
    ExternalSecretMappingDetail,
    ExternalSecretProviderDetail,
    ExternalSecretSourceDetail,
    GatewayClassProviderDetail,
    GatewayRouteBackendDetail,
    GatewayRouteFilterDetail,
    GatewayRouteMatchDetail,
    GatewayRouteParentStatusDetail,
    GatewayRouteRuleDetail,
    GcpAdditionalDiskDetail,
    GcpAuthorizedNetworkDetail,
    GcpMachineProviderDetail,
    GcpManagedControlPlaneProviderDetail,
    GcpManagedMachinePoolProviderDetail,
    GrpcRouteProviderDetail,
    HttpRouteProviderDetail,
    JobProviderDetail,
    KarpenterBlockDeviceDetail,
    KarpenterCapacityDetail,
    KarpenterDisruptionBudgetDetail,
    KarpenterEc2NodeClassProviderDetail,
    KarpenterMetadataOptionsDetail,
    KarpenterNodeClaimProviderDetail,
    KarpenterNodePoolProviderDetail,
    KarpenterResolvedAmiDetail,
    KarpenterResolvedNetworkDetail,
    KarpenterSelectorTermDetail,
    KedaScaledJobProviderDetail,
    KedaScaledObjectProviderDetail,
    KedaScalingPolicyDetail,
    KedaTriggerDetail,
    PersistentVolumeClaimProviderDetail,
    PrometheusRuleEntryDetail,
    PrometheusRuleGroupDetail,
    PrometheusRuleProviderDetail,
    ProviderAddress,
    ProviderCondition,
    ProviderKeyValue,
    ProviderNamedReference,
    ProviderReference,
    ProviderReplicas,
    ProviderRequirementDetail,
    ProviderScaling,
    ProviderTaint,
    ResourceProviderDetail,
    SbomComponentDetail,
    SbomReportProviderDetail,
    SealedSecretProviderDetail,
    SecretProviderDetail,
    SecretStoreProviderDetail,
    SecuritySeveritySummaryDetail,
    TcpRouteProviderDetail,
    TlsRouteProviderDetail,
    VulnerabilityFindingDetail,
    VulnerabilityReportProviderDetail,
    WorkflowExecutionNodeDetail,
    WorkflowProviderDetail,
)

INFRASTRUCTURE_GROUP = "infrastructure.cluster.x-k8s.io"
CONTROL_PLANE_GROUP = "controlplane.cluster.x-k8s.io"
CAPI_GROUP = "cluster.x-k8s.io"
CERT_MANAGER_GROUP = "cert-manager.io"
COMPLIANCE_GROUP = "aquasecurity.github.io"
ARGO_GROUP = "argoproj.io"
EXTERNAL_SECRETS_GROUP = "external-secrets.io"
SEALED_SECRETS_GROUP = "sealedsecrets.bitnami.com"
GATEWAY_GROUP = "gateway.networking.k8s.io"
BATCH_GROUP = "batch"
KARPENTER_GROUP = "karpenter.sh"
KARPENTER_AWS_GROUP = "karpenter.k8s.aws"
KEDA_GROUP = "keda.sh"
PROMETHEUS_GROUP = "monitoring.coreos.com"
MAX_COLLECTION_ITEMS = 100
MAX_TEXT_LENGTH = 2_000
MAX_ROUTE_RULES = 50
MAX_ROUTE_ITEMS = 50
MAX_PROMETHEUS_GROUPS = 50
MAX_PROMETHEUS_RULES = 500
MAX_SBOM_COMPONENTS = 1_000
MAX_VULNERABILITIES = 500
MAX_WORKFLOW_NODES = 500
MAX_SAFE_INTEGER = 9_007_199_254_740_991
COMPLIANCE_SEVERITY_ORDER = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "UNKNOWN": 4,
}
SENSITIVE_ROUTE_VALUE_NAMES = frozenset(
    {
        "api-key",
        "authorization",
        "cookie",
        "password",
        "proxy-authorization",
        "secret",
        "set-cookie",
        "token",
        "x-api-key",
    }
)
SENSITIVE_DYNAMIC_KEY_PARTS = (
    "api-key",
    "apikey",
    "auth",
    "certificate",
    "connection",
    "credential",
    "key",
    "password",
    "private",
    "sas",
    "secret",
    "token",
)


class ProviderDetailProjector(Protocol):
    def __call__(self, raw: Mapping[str, Any]) -> ResourceProviderDetail: ...


class ProviderDetailMatcher(Protocol):
    def __call__(
        self, resource: Mapping[str, Any], raw: Mapping[str, Any]
    ) -> ResourceProviderDetail | None: ...


def provider_detail_projection(resource: Mapping[str, Any]) -> ResourceProviderDetail | None:
    """Return one redacted projection only for an exact supported API-group/kind pair."""
    raw = _mapping(resource.get("raw"))
    if not raw:
        return None
    kind = _text(resource.get("kind")) or ""
    projector = PROVIDER_DETAIL_PROJECTORS.get((_api_group(resource.get("api_version")), kind))
    if projector is not None:
        return projector(raw)
    for matcher in PROVIDER_DETAIL_MATCHERS:
        if detail := matcher(resource, raw):
            return detail
    return None


def _aws_machine(raw: Mapping[str, Any]) -> AwsMachineProviderDetail:
    return AwsMachineProviderDetail(
        instance_type=_text_at(raw, "spec", "instanceType"),
        instance_id=_text_at(raw, "spec", "instanceID"),
        instance_state=_text_at(raw, "status", "instanceState"),
        provider_id=_text_at(raw, "spec", "providerID"),
        iam_instance_profile=_text_at(raw, "spec", "iamInstanceProfile"),
        ssh_key_name=_text_at(raw, "spec", "sshKeyName"),
        subnet_id=_text_at(raw, "spec", "subnet", "id"),
        secrets_backend=_text_at(raw, "spec", "cloudInit", "secureSecretsBackend"),
        addresses=[
            ProviderAddress(type=address_type, address=value)
            for item in _mapping_items_at(raw, "status", "addresses")
            if (address_type := _text(item.get("type"))) is not None
            and (value := _text(item.get("address"))) is not None
        ],
        conditions=_conditions(raw),
    )


def _aws_managed_cluster(raw: Mapping[str, Any]) -> AwsManagedClusterProviderDetail:
    host = _text_at(raw, "spec", "controlPlaneEndpoint", "host")
    port = _int_at(raw, "spec", "controlPlaneEndpoint", "port")
    endpoint = None if host is None else f"{host}:{port}" if port not in (None, 443) else host
    return AwsManagedClusterProviderDetail(
        endpoint=endpoint,
        failure_domains=_mapping_keys_at(raw, "status", "failureDomains"),
        conditions=_conditions(raw),
    )


def _aws_managed_control_plane(
    raw: Mapping[str, Any],
) -> AwsManagedControlPlaneProviderDetail:
    endpoint_access = _endpoint_access(raw)
    identity_kind = _text_at(raw, "spec", "identityRef", "kind")
    identity_name = _text_at(raw, "spec", "identityRef", "name")
    identity = (
        f"{identity_kind}/{identity_name}"
        if identity_kind is not None and identity_name is not None
        else None
    )
    status_addons = {
        name: item
        for item in _mapping_items_at(raw, "status", "addons")
        if (name := _text(item.get("name"))) is not None
    }
    addons: list[AwsAddon] = []
    for item in _mapping_items_at(raw, "spec", "addons"):
        name = _text(item.get("name"))
        if name is None:
            continue
        current = status_addons.get(name, {})
        addons.append(
            AwsAddon(
                name=name,
                requested_version=_text(item.get("version")),
                current_version=_text(current.get("currentVersion"))
                or _text(current.get("version")),
                status=_text(current.get("status")),
            )
        )
    subnets = [
        AwsSubnet(
            id=_text(item.get("id")) or _text(item.get("resourceID")),
            availability_zone=_text(item.get("availabilityZone")),
            public=_bool(item.get("isPublic")),
            cidr_block=_text(item.get("cidrBlock")),
        )
        for item in _mapping_items_at(raw, "spec", "network", "subnets")
    ]
    security_groups = [
        AwsSecurityGroup(
            role=role,
            id=_text(item.get("id")),
            name=_text(item.get("name")),
        )
        for role, item in _mapping_at(raw, "status", "networkStatus", "securityGroups").items()
        if isinstance(role, str) and isinstance(item, Mapping)
    ][:MAX_COLLECTION_ITEMS]
    return AwsManagedControlPlaneProviderDetail(
        cluster_name=_text_at(raw, "spec", "eksClusterName"),
        region=_text_at(raw, "spec", "region"),
        version=_text_at(raw, "spec", "version"),
        endpoint_access=endpoint_access,
        role_name=_text_at(raw, "spec", "roleName"),
        identity=identity,
        vpc_id=_text_at(raw, "spec", "network", "vpc", "id"),
        vpc_cidr_block=_text_at(raw, "spec", "network", "vpc", "cidrBlock"),
        subnets=subnets,
        security_groups=security_groups,
        nat_gateway_ips=_text_items_at(raw, "status", "networkStatus", "natGatewaysIPs"),
        failure_domains=_mapping_keys_at(raw, "status", "failureDomains"),
        addons=addons,
        conditions=_conditions(raw),
    )


def _aws_managed_machine_pool(
    raw: Mapping[str, Any],
) -> AwsManagedMachinePoolProviderDetail:
    return AwsManagedMachinePoolProviderDetail(
        node_group_name=_text_at(raw, "spec", "eksNodegroupName"),
        instance_type=_text_at(raw, "spec", "instanceType"),
        ami_type=_text_at(raw, "spec", "amiType"),
        capacity_type=_text_at(raw, "spec", "capacityType"),
        role_name=_text_at(raw, "spec", "roleName"),
        scaling=ProviderScaling(
            minimum=_int_at(raw, "spec", "scaling", "minSize"),
            maximum=_int_at(raw, "spec", "scaling", "maxSize"),
            current=_int_at(raw, "status", "replicas"),
        ),
        max_unavailable=_int_at(raw, "spec", "updateConfig", "maxUnavailable"),
        subnet_ids=_text_items_at(raw, "spec", "subnetIDs"),
        labels=_key_values_at(raw, "spec", "labels"),
        conditions=_conditions(raw),
    )


def _azure_machine(raw: Mapping[str, Any]) -> AzureMachineProviderDetail:
    return AzureMachineProviderDetail(
        vm_size=_text_at(raw, "spec", "vmSize"),
        availability_zone=_text_at(raw, "spec", "failureDomain"),
        os_type=_text_at(raw, "spec", "osDisk", "osType"),
        os_disk_size_gb=_int_at(raw, "spec", "osDisk", "diskSizeGB"),
        provider_id=_text_at(raw, "spec", "providerID"),
        subnet_name=_text_at(raw, "spec", "subnetName"),
        conditions=_conditions(raw),
    )


def _azure_managed_control_plane(
    raw: Mapping[str, Any],
) -> AzureManagedControlPlaneProviderDetail:
    return AzureManagedControlPlaneProviderDetail(
        location=_text_at(raw, "spec", "location"),
        resource_group_name=_text_at(raw, "spec", "resourceGroupName"),
        version=_text_at(raw, "spec", "version"),
        sku_tier=_text_at(raw, "spec", "sku", "tier"),
        dns_prefix=_text_at(raw, "spec", "dnsPrefix"),
        subscription_id=_text_at(raw, "spec", "subscriptionID"),
        network_plugin=_text_at(raw, "spec", "networkPlugin"),
        network_policy=_text_at(raw, "spec", "networkPolicy"),
        private_cluster=_bool_at(raw, "spec", "apiServerAccessProfile", "enablePrivateCluster"),
        dns_service_ip=_text_at(raw, "spec", "dnsServiceIP"),
        load_balancer_sku=_text_at(raw, "spec", "loadBalancerSKU"),
        upgrade_channel=_text_at(raw, "spec", "autoUpgradeProfile", "upgradeChannel"),
        authorized_ip_ranges=_text_items_at(
            raw, "spec", "apiServerAccessProfile", "authorizedIPRanges"
        ),
        conditions=_conditions(raw),
    )


def _azure_managed_machine_pool(
    raw: Mapping[str, Any],
) -> AzureManagedMachinePoolProviderDetail:
    taints = [
        ProviderTaint(
            key=key,
            value=_text(item.get("value")),
            effect=_text(item.get("effect")),
        )
        for item in _mapping_items_at(raw, "spec", "taints")
        if (key := _text(item.get("key"))) is not None
    ]
    return AzureManagedMachinePoolProviderDetail(
        pool_name=_text_at(raw, "spec", "name"),
        vm_size=_text_at(raw, "spec", "sku"),
        mode=_text_at(raw, "spec", "mode"),
        os_type=_text_at(raw, "spec", "osType"),
        os_disk_type=_text_at(raw, "spec", "osDiskType"),
        os_disk_size_gb=_int_at(raw, "spec", "osDiskSizeGB"),
        priority=_text_at(raw, "spec", "scaleSetPriority"),
        max_pods=_int_at(raw, "spec", "maxPods"),
        scaling=ProviderScaling(
            minimum=_int_at(raw, "spec", "scaling", "minSize"),
            maximum=_int_at(raw, "spec", "scaling", "maxSize"),
            current=_int_at(raw, "status", "replicas"),
        ),
        scale_down_mode=_text_at(raw, "spec", "scaleDownMode"),
        availability_zones=_text_items_at(raw, "spec", "availabilityZones"),
        labels=_key_values_at(raw, "spec", "nodeLabels"),
        taints=taints,
        conditions=_conditions(raw),
    )


def _capi_cluster(raw: Mapping[str, Any]) -> CapiClusterProviderDetail:
    host = _text_at(raw, "spec", "controlPlaneEndpoint", "host")
    port = _int_at(raw, "spec", "controlPlaneEndpoint", "port")
    infrastructure_ref = _reference_at(raw, "spec", "infrastructureRef")
    return CapiClusterProviderDetail(
        phase=_text_at(raw, "status", "phase"),
        version=_text_at(raw, "spec", "topology", "version"),
        cluster_class=_text_at(raw, "spec", "topology", "class"),
        endpoint=None if host is None else f"{host}:{port}" if port is not None else host,
        provider=_provider_from_kind(infrastructure_ref.kind) if infrastructure_ref else None,
        paused=_bool_at(raw, "spec", "paused") is True,
        control_plane=ProviderReplicas(
            desired=_first_present_int(
                _int_at(raw, "status", "controlPlane", "desiredReplicas"),
                _int_at(raw, "spec", "topology", "controlPlane", "replicas"),
            ),
            ready=_int_at(raw, "status", "controlPlane", "readyReplicas"),
            available=_int_at(raw, "status", "controlPlane", "availableReplicas"),
            up_to_date=_int_at(raw, "status", "controlPlane", "upToDateReplicas"),
        ),
        workers=ProviderReplicas(
            desired=_int_at(raw, "status", "workers", "desiredReplicas"),
            ready=_int_at(raw, "status", "workers", "readyReplicas"),
            available=_int_at(raw, "status", "workers", "availableReplicas"),
            up_to_date=_int_at(raw, "status", "workers", "upToDateReplicas"),
        ),
        control_plane_ref=_reference_at(raw, "spec", "controlPlaneRef"),
        infrastructure_ref=infrastructure_ref,
        conditions=_conditions(raw),
    )


def _capi_kubeadm_control_plane(
    raw: Mapping[str, Any],
) -> CapiKubeadmControlPlaneProviderDetail:
    machine_template = _mapping_at(raw, "spec", "machineTemplate")
    remediation = _mapping_at(raw, "status", "lastRemediation")
    initialized = _bool_at(raw, "status", "initialized")
    if initialized is None:
        initialized = _condition_truth(raw, "Initialized")
    return CapiKubeadmControlPlaneProviderDetail(
        cluster_name=_cluster_name(raw),
        version=_text_at(raw, "spec", "version"),
        initialized=initialized,
        update_strategy=(
            "RollingUpdate"
            if _value_at(raw, "spec", "rolloutStrategy") is not None
            or _value_at(raw, "spec", "upgradeAfter") is not None
            else None
        ),
        replicas=_standard_replicas(raw),
        infrastructure_ref=_reference_at(machine_template, "infrastructureRef"),
        node_drain_timeout=_text(machine_template.get("nodeDrainTimeout")),
        node_volume_detach_timeout=_text(machine_template.get("nodeVolumeDetachTimeout")),
        node_deletion_timeout=_text(machine_template.get("nodeDeletionTimeout")),
        certificate_sans=_text_items_at(
            raw,
            "spec",
            "kubeadmConfigSpec",
            "clusterConfiguration",
            "certSANs",
        ),
        remediation_machine=_text(remediation.get("machine")),
        remediation_retry_count=_int(remediation.get("retryCount")),
        remediation_timestamp=_text(remediation.get("timestamp")),
        conditions=_conditions(raw),
    )


def _capi_machine_deployment(raw: Mapping[str, Any]) -> CapiMachineDeploymentProviderDetail:
    template = _mapping_at(raw, "spec", "template", "spec")
    return CapiMachineDeploymentProviderDetail(
        phase=_text_at(raw, "status", "phase"),
        cluster_name=_cluster_name(raw),
        version=_text_at(raw, "spec", "template", "spec", "version"),
        paused=_bool_at(raw, "spec", "paused") is True,
        replicas=_standard_replicas(raw),
        strategy_type=_text_at(raw, "spec", "strategy", "type"),
        max_surge=_scalar_text_at(raw, "spec", "strategy", "rollingUpdate", "maxSurge"),
        max_unavailable=_scalar_text_at(raw, "spec", "strategy", "rollingUpdate", "maxUnavailable"),
        infrastructure_ref=_reference_at(template, "infrastructureRef"),
        bootstrap_ref=_reference_at(template, "bootstrap", "configRef"),
        conditions=_conditions(raw),
    )


def _capi_machine_health_check(raw: Mapping[str, Any]) -> CapiMachineHealthCheckProviderDetail:
    unhealthy = [
        CapiUnhealthyCondition(
            type=condition_type,
            status=_text(item.get("status")),
            timeout=_text(item.get("timeout")),
        )
        for path in (
            ("spec", "unhealthyConditions"),
            ("spec", "unhealthyNodeConditions"),
            ("spec", "unhealthyMachineConditions"),
        )
        for item in _mapping_items_at(raw, *path)
        if (condition_type := _text(item.get("type"))) is not None
    ][:MAX_COLLECTION_ITEMS]
    return CapiMachineHealthCheckProviderDetail(
        cluster_name=_text_at(raw, "spec", "clusterName") or _cluster_name(raw),
        expected_machines=_int_at(raw, "status", "expectedMachines"),
        current_healthy=_int_at(raw, "status", "currentHealthy"),
        remediations_allowed=_int_at(raw, "status", "remediationsAllowed"),
        node_startup_timeout=_text_at(raw, "spec", "nodeStartupTimeout"),
        max_unhealthy=_scalar_text_at(raw, "spec", "maxUnhealthy"),
        unhealthy_range=_text_at(raw, "spec", "unhealthyRange"),
        selector=_key_values_at(raw, "spec", "selector", "matchLabels"),
        unhealthy_conditions=unhealthy,
        remediation_template=_reference_at(raw, "spec", "remediationTemplate"),
        conditions=_conditions(raw),
    )


def _capi_machine_pool(raw: Mapping[str, Any]) -> CapiMachinePoolProviderDetail:
    template = _mapping_at(raw, "spec", "template", "spec")
    return CapiMachinePoolProviderDetail(
        phase=_text_at(raw, "status", "phase"),
        cluster_name=_cluster_name(raw),
        min_ready_seconds=_int_at(raw, "spec", "minReadySeconds"),
        replicas=_standard_replicas(raw),
        infrastructure_ref=_reference_at(template, "infrastructureRef"),
        bootstrap_ref=_reference_at(template, "bootstrap", "configRef"),
        conditions=_conditions(raw),
    )


def _capi_machine(raw: Mapping[str, Any]) -> CapiMachineProviderDetail:
    provider_id = _text_at(raw, "spec", "providerID")
    provider, region, instance_id = _provider_id_parts(provider_id)
    labels = _mapping_at(raw, "metadata", "labels")
    control_plane = (
        "cluster.x-k8s.io/control-plane" in labels
        or _text(labels.get("cluster.x-k8s.io/control-plane-name")) is not None
    )
    return CapiMachineProviderDetail(
        phase=_text_at(raw, "status", "phase"),
        role="control-plane" if control_plane else "worker",
        cluster_name=_cluster_name(raw),
        version=_text_at(raw, "spec", "version"),
        failure_domain=_text_at(raw, "spec", "failureDomain"),
        provider=provider,
        provider_id=provider_id,
        provider_region=region,
        provider_instance_id=instance_id,
        node_name=_text_at(raw, "status", "nodeRef", "name"),
        node_uid=_text_at(raw, "status", "nodeRef", "uid"),
        bootstrap_ref=_reference_at(raw, "spec", "bootstrap", "configRef"),
        infrastructure_ref=_reference_at(raw, "spec", "infrastructureRef"),
        addresses=[
            ProviderAddress(type=address_type, address=address)
            for item in _mapping_items_at(raw, "status", "addresses")
            if (address_type := _text(item.get("type"))) is not None
            and (address := _text(item.get("address"))) is not None
        ],
        os_image=_text_at(raw, "status", "nodeInfo", "osImage"),
        architecture=_text_at(raw, "status", "nodeInfo", "architecture"),
        kernel_version=_text_at(raw, "status", "nodeInfo", "kernelVersion"),
        container_runtime_version=_text_at(raw, "status", "nodeInfo", "containerRuntimeVersion"),
        kubelet_version=_text_at(raw, "status", "nodeInfo", "kubeletVersion"),
        conditions=_conditions(raw),
    )


def _capi_machine_set(raw: Mapping[str, Any]) -> CapiMachineSetProviderDetail:
    template = _mapping_at(raw, "spec", "template", "spec")
    return CapiMachineSetProviderDetail(
        cluster_name=_cluster_name(raw),
        delete_policy=_text_at(raw, "spec", "deletePolicy"),
        min_ready_seconds=_int_at(raw, "spec", "minReadySeconds"),
        replicas=_standard_replicas(raw),
        infrastructure_ref=_reference_at(template, "infrastructureRef"),
        bootstrap_ref=_reference_at(template, "bootstrap", "configRef"),
        conditions=_conditions(raw),
    )


def _certificate(raw: Mapping[str, Any]) -> CertificateProviderDetail:
    private_key = _mapping_at(raw, "spec", "privateKey")
    return CertificateProviderDetail(
        ready=_condition_truth(raw, "Ready"),
        secret_name=_text_at(raw, "spec", "secretName"),
        revision=_int_at(raw, "status", "revision"),
        is_ca=_bool_at(raw, "spec", "isCA"),
        duration=_text_at(raw, "spec", "duration"),
        renew_before=_text_at(raw, "spec", "renewBefore"),
        not_before=_text_at(raw, "status", "notBefore"),
        not_after=_text_at(raw, "status", "notAfter"),
        renewal_time=_text_at(raw, "status", "renewalTime"),
        failed_issuance_attempts=_int_at(raw, "status", "failedIssuanceAttempts"),
        last_failure_time=_text_at(raw, "status", "lastFailureTime"),
        private_key=(
            CertificatePrivateKeyDetail(
                algorithm=_text(private_key.get("algorithm")),
                size=_int(private_key.get("size")),
                encoding=_text(private_key.get("encoding")),
                rotation_policy=_text(private_key.get("rotationPolicy")),
            )
            if private_key
            else None
        ),
        dns_names=_text_items_at(raw, "spec", "dnsNames"),
        issuer_ref=_named_reference_at(raw, "spec", "issuerRef"),
        usages=_text_items_at(raw, "spec", "usages"),
        conditions=_conditions(raw),
    )


def _certificate_request(raw: Mapping[str, Any]) -> CertificateRequestProviderDetail:
    owner_certificate: ProviderNamedReference | None = None
    for item in _mapping_items_at(raw, "metadata", "ownerReferences"):
        if _text(item.get("kind")) == "Certificate":
            owner_certificate = _named_reference(item)
            if owner_certificate is not None:
                break
    return CertificateRequestProviderDetail(
        ready=_condition_truth(raw, "Ready"),
        approved=_condition_truth(raw, "Approved"),
        denied=_condition_truth(raw, "Denied"),
        issuer_ref=_named_reference_at(raw, "spec", "issuerRef"),
        owner_certificate=owner_certificate,
        duration=_text_at(raw, "spec", "duration"),
        usages=_text_items_at(raw, "spec", "usages"),
        certificate_issued=_present_at(raw, "status", "certificate"),
        conditions=_conditions(raw),
    )


def _cluster_compliance_report(
    raw: Mapping[str, Any],
) -> ClusterComplianceReportProviderDetail:
    definitions = {
        control_id: item
        for item in _mapping_items_at(raw, "spec", "compliance", "controls")
        if (control_id := _text(item.get("id"))) is not None
    }
    controls: list[ComplianceControlDetail] = []
    seen_control_ids: set[str] = set()
    for item in _mapping_items_at(raw, "status", "summaryReport", "controlCheck"):
        control_id = _text(item.get("id"))
        if control_id is None or control_id in seen_control_ids:
            continue
        seen_control_ids.add(control_id)
        definition = definitions.get(control_id, {})
        controls.append(
            ComplianceControlDetail(
                id=control_id,
                name=_text(item.get("name")),
                description=_text(definition.get("description")),
                severity=_text(item.get("severity")),
                total_pass=_int(item.get("totalPass")),
                total_fail=_int(item.get("totalFail")),
                check_ids=[
                    check_id
                    for check in _mapping_items(definition.get("checks"))
                    if (check_id := _text(check.get("id"))) is not None
                ],
            )
        )
    controls.sort(
        key=lambda control: (
            (control.total_fail or 0) == 0,
            COMPLIANCE_SEVERITY_ORDER.get((control.severity or "").upper(), 99),
            control.id,
        )
    )
    return ClusterComplianceReportProviderDetail(
        framework_id=_text_at(raw, "spec", "compliance", "id"),
        framework_title=_text_at(raw, "spec", "compliance", "title"),
        framework_description=_text_at(raw, "spec", "compliance", "description"),
        framework_version=_text_at(raw, "spec", "compliance", "version"),
        platform=_text_at(raw, "spec", "compliance", "platform"),
        updated_at=_text_at(raw, "status", "updateTimestamp"),
        pass_count=_int_at(raw, "status", "summary", "passCount"),
        fail_count=_int_at(raw, "status", "summary", "failCount"),
        controls=controls,
        conditions=_conditions(raw),
    )


def _crossplane_composite(
    resource: Mapping[str, Any], raw: Mapping[str, Any]
) -> CrossplaneCompositeProviderDetail | None:
    del resource
    spec = _mapping_at(raw, "spec")
    crossplane = _mapping(spec.get("crossplane"))
    if _mapping(spec.get("providerConfigRef")) or _mapping(crossplane.get("providerConfigRef")):
        return None
    resource_refs = crossplane.get("resourceRefs")
    if not isinstance(resource_refs, list):
        resource_refs = spec.get("resourceRefs")
    claim = bool(_mapping(spec.get("resourceRef")) and _mapping(spec.get("compositionRef")))
    if not isinstance(resource_refs, list) and not claim:
        return None
    composition = _mapping(crossplane.get("compositionRef")) or _mapping(spec.get("compositionRef"))
    composition_revision = _mapping(crossplane.get("compositionRevisionRef")) or _mapping(
        spec.get("compositionRevisionRef")
    )
    return CrossplaneCompositeProviderDetail(
        claim=claim,
        paused=_text_at(raw, "metadata", "annotations", "crossplane.io/paused") == "true",
        composition_ref=_named_reference(composition),
        composition_revision_ref=_named_reference(composition_revision),
        composition_update_policy=_text(crossplane.get("compositionUpdatePolicy"))
        or _text(spec.get("compositionUpdatePolicy")),
        bound_resource_ref=_named_reference(_mapping(spec.get("resourceRef"))) if claim else None,
        composed_resource_refs=[
            reference
            for item in _mapping_items(resource_refs)
            if (reference := _named_reference(item)) is not None
        ],
        conditions=_conditions(raw),
    )


def _crossplane_managed_resource(
    resource: Mapping[str, Any], raw: Mapping[str, Any]
) -> CrossplaneManagedResourceProviderDetail | None:
    spec = _mapping_at(raw, "spec")
    crossplane = _mapping(spec.get("crossplane"))
    provider_config = _mapping(spec.get("providerConfigRef")) or _mapping(
        crossplane.get("providerConfigRef")
    )
    for_provider = _mapping(spec.get("forProvider"))
    if not provider_config or not for_provider:
        return None
    status_at_provider = _mapping_at(raw, "status", "atProvider")
    composing_ref = next(
        (
            _named_reference_with_namespace(item, _text_at(raw, "metadata", "namespace"))
            for item in _mapping_items(_value_at(raw, "metadata", "ownerReferences"))
            if _text(item.get("apiVersion")) is not None
            and (
                _text(item.get("kind")) == "CompositeResourceDefinition"
                or _text(item.get("controller")) == "true"
                or item.get("controller") is True
            )
        ),
        None,
    )
    return CrossplaneManagedResourceProviderDetail(
        api_group=_api_group(resource.get("api_version")) or None,
        kind=_text(resource.get("kind")) or "ManagedResource",
        external_name=_text_at(raw, "metadata", "annotations", "crossplane.io/external-name"),
        management_policies=_text_items(spec.get("managementPolicies")),
        deletion_policy=_text(spec.get("deletionPolicy")),
        paused=_text_at(raw, "metadata", "annotations", "crossplane.io/paused") == "true",
        provider_config_ref=_named_reference(provider_config),
        composing_resource_ref=composing_ref,
        observed_spec_fields=sorted(for_provider)[:MAX_COLLECTION_ITEMS],
        observed_status_fields=sorted(status_at_provider)[:MAX_COLLECTION_ITEMS],
        conditions=_conditions(raw),
    )


def _cron_workflow(raw: Mapping[str, Any]) -> CronWorkflowProviderDetail:
    schedules = _text_items_at(raw, "spec", "schedules")
    if not schedules and (schedule := _text_at(raw, "spec", "schedule")) is not None:
        schedules = [schedule]
    workflow_spec = _mapping_at(raw, "spec", "workflowSpec")
    template_ref = _mapping(workflow_spec.get("workflowTemplateRef"))
    template_name = _text(template_ref.get("name")) or _text(template_ref.get("template"))
    template_reference = (
        ProviderNamedReference(name=template_name) if template_name is not None else None
    )
    return CronWorkflowProviderDetail(
        schedules=schedules,
        timezone=_text_at(raw, "spec", "timezone"),
        suspended=_bool_at(raw, "spec", "suspend"),
        concurrency_policy=_text_at(raw, "spec", "concurrencyPolicy"),
        last_scheduled_time=_text_at(raw, "status", "lastScheduledTime"),
        active_workflows=[
            reference
            for item in _mapping_items_at(raw, "status", "active")
            if (reference := _named_reference(item)) is not None
        ],
        workflow_template_ref=template_reference,
        workflow_template_cluster_scope=_bool(template_ref.get("clusterScope")),
        entrypoint=_text(workflow_spec.get("entrypoint")),
        argument_count=_collection_length_at(workflow_spec, "arguments", "parameters"),
        template_count=_collection_length_at(workflow_spec, "templates"),
        successful_history_limit=_int_at(raw, "spec", "successfulJobsHistoryLimit"),
        failed_history_limit=_int_at(raw, "spec", "failedJobsHistoryLimit"),
        starting_deadline_seconds=_int_at(raw, "spec", "startingDeadlineSeconds"),
        conditions=_conditions(raw),
    )


def _external_secret(raw: Mapping[str, Any]) -> ExternalSecretProviderDetail:
    target = _mapping_at(raw, "spec", "target")
    template = _mapping(target.get("template"))
    store = _mapping_at(raw, "spec", "secretStoreRef")
    target_name = _text(target.get("name")) or _text_at(raw, "metadata", "name")
    return ExternalSecretProviderDetail(
        ready=_condition_truth(raw, "Ready"),
        last_sync_time=_text_at(raw, "status", "refreshTime"),
        refresh_interval=_text_at(raw, "spec", "refreshInterval"),
        target_name=target_name,
        synced_resource_version=_text_at(raw, "status", "syncedResourceVersion"),
        binding_name=_text_at(raw, "status", "binding", "name"),
        store_name=_text(store.get("name")),
        store_kind=_text(store.get("kind")),
        mappings=[
            ExternalSecretMappingDetail(
                secret_key=_text(item.get("secretKey")),
                remote_key=_text_at(item, "remoteRef", "key"),
                remote_property=_text_at(item, "remoteRef", "property"),
                remote_version=_text_at(item, "remoteRef", "version"),
            )
            for item in _mapping_items_at(raw, "spec", "data")
        ],
        data_sources=[
            _external_secret_source(item) for item in _mapping_items_at(raw, "spec", "dataFrom")
        ],
        target_creation_policy=_text(target.get("creationPolicy")),
        target_deletion_policy=_text(target.get("deletionPolicy")),
        template_type=_text(template.get("type")),
        template_engine_version=_text(template.get("engineVersion")),
        template_labels=_key_values_at(template, "metadata", "labels"),
        template_annotations=_key_values_at(template, "metadata", "annotations"),
        conditions=_conditions(raw),
    )


def _external_secret_source(item: Mapping[str, Any]) -> ExternalSecretSourceDetail:
    extract = _mapping(item.get("extract"))
    if extract:
        return ExternalSecretSourceDetail(type="extract", detail=_text(extract.get("key")))
    find = _mapping(item.get("find"))
    if find:
        return ExternalSecretSourceDetail(
            type="find",
            detail=_text_at(find, "name", "regexp")
            or ("tags" if _mapping(find.get("tags")) else "name"),
        )
    source_ref = _mapping(item.get("sourceRef"))
    if source_ref:
        return ExternalSecretSourceDetail(
            type="source-ref",
            detail=_joined_text(source_ref.get("kind"), source_ref.get("name")),
        )
    return ExternalSecretSourceDetail(type="unknown")


def _persistent_volume_claim(raw: Mapping[str, Any]) -> PersistentVolumeClaimProviderDetail:
    annotations = _mapping_at(raw, "metadata", "annotations")
    return PersistentVolumeClaimProviderDetail(
        phase=_text_at(raw, "status", "phase"),
        capacity=_scalar_text_at(raw, "status", "capacity", "storage"),
        requested=_scalar_text_at(raw, "spec", "resources", "requests", "storage"),
        storage_class_name=_text_at(raw, "spec", "storageClassName"),
        access_modes=_text_items_at(raw, "spec", "accessModes"),
        volume_mode=_text_at(raw, "spec", "volumeMode"),
        volume_name=_text_at(raw, "spec", "volumeName"),
        provisioner=_text(annotations.get("volume.kubernetes.io/storage-provisioner")),
        selected_node=_text(annotations.get("volume.kubernetes.io/selected-node")),
        bind_completed=_bool_text(annotations.get("pv.kubernetes.io/bind-completed")),
        conditions=_conditions(raw),
    )


def _sealed_secret(raw: Mapping[str, Any]) -> SealedSecretProviderDetail:
    annotations = _mapping_at(raw, "metadata", "annotations")
    template = _mapping_at(raw, "spec", "template")
    template_metadata = _mapping(template.get("metadata"))
    return SealedSecretProviderDetail(
        synced=_condition_truth(raw, "Synced"),
        target_secret_name=_text_at(template_metadata, "name") or _text_at(raw, "metadata", "name"),
        secret_type=_text(template.get("type")),
        scope=(
            "cluster-wide"
            if annotations.get("sealedsecrets.bitnami.com/cluster-wide") == "true"
            else "namespace-wide"
            if annotations.get("sealedsecrets.bitnami.com/namespace-wide") == "true"
            else "strict"
        ),
        observed_generation=_int_at(raw, "status", "observedGeneration"),
        encrypted_keys=sorted(_mapping_at(raw, "spec", "encryptedData"))[:MAX_COLLECTION_ITEMS],
        template_labels=_safe_key_values(_mapping(template_metadata.get("labels"))),
        template_annotations=_safe_key_values(_mapping(template_metadata.get("annotations"))),
        conditions=_conditions(raw),
    )


def _secret(raw: Mapping[str, Any]) -> SecretProviderDetail:
    return SecretProviderDetail(
        secret_type=_text_at(raw, "type"),
        immutable=_bool_at(raw, "immutable"),
        key_names=sorted(_mapping_at(raw, "data"))[:MAX_COLLECTION_ITEMS],
        conditions=[],
    )


SECRET_STORE_PROVIDER_LABELS = {
    "aws": "AWS Secrets Manager",
    "azurekv": "Azure Key Vault",
    "gcpsm": "GCP Secret Manager",
    "vault": "HashiCorp Vault",
    "kubernetes": "Kubernetes",
    "oracle": "Oracle Vault",
    "ibm": "IBM Secrets Manager",
    "doppler": "Doppler",
    "onepassword": "1Password",
    "akeyless": "Akeyless",
}


def _secret_store(raw: Mapping[str, Any]) -> SecretStoreProviderDetail:
    provider = _mapping_at(raw, "spec", "provider")
    provider_key = next(
        (key for key in SECRET_STORE_PROVIDER_LABELS if _mapping(provider.get(key))),
        next(iter(sorted(provider)), None),
    )
    details = (
        _secret_store_provider_details(provider_key, _mapping(provider.get(provider_key)))
        if provider_key is not None
        else []
    )
    return SecretStoreProviderDetail(
        cluster_scope=_text_at(raw, "kind") == "ClusterSecretStore",
        ready=_condition_truth(raw, "Ready"),
        provider_key=provider_key,
        provider_type=SECRET_STORE_PROVIDER_LABELS.get(provider_key, provider_key)
        if provider_key is not None
        else None,
        provider_details=details,
        controller=_text_at(raw, "spec", "controller"),
        max_retries=_int_at(raw, "spec", "retrySettings", "maxRetries"),
        retry_interval=_text_at(raw, "spec", "retrySettings", "retryInterval"),
        conditions=_conditions(raw),
    )


def _cluster_secret_store(raw: Mapping[str, Any]) -> SecretStoreProviderDetail:
    return _secret_store(raw).model_copy(update={"cluster_scope": True})


def _secret_store_provider_details(
    provider_key: str, provider: Mapping[str, Any]
) -> list[ProviderKeyValue]:
    paths: dict[str, tuple[tuple[str, ...], ...]] = {
        "aws": (
            ("region",),
            ("service",),
            ("auth", "jwt", "serviceAccountRef", "name"),
        ),
        "azurekv": (("vaultUrl",), ("tenantId",), ("authType",), ("environmentType",)),
        "gcpsm": (("projectID",), ("location",)),
        "vault": (("server",), ("path",), ("version",), ("namespace",)),
        "kubernetes": (("server", "url"), ("remoteNamespace",)),
        "oracle": (("vault",), ("region",)),
        "ibm": (("serviceUrl",),),
        "doppler": (("project",), ("config",)),
        "onepassword": (("connectHost",),),
        "akeyless": (("akeylessGWApiURL",),),
    }
    result: list[ProviderKeyValue] = []
    for path in paths.get(provider_key, ()):
        value = _text_at(provider, *path)
        if value is not None:
            result.append(ProviderKeyValue(key=".".join(path), value=value))
    return result[:MAX_COLLECTION_ITEMS]


def _workflow(raw: Mapping[str, Any]) -> WorkflowProviderDetail:
    raw_nodes = _mapping_at(raw, "status", "nodes")
    projected_nodes = _workflow_execution_nodes(raw_nodes)
    phase = _text_at(raw, "status", "phase") or "Unknown"
    problem_summaries = _workflow_problem_summaries(phase, raw, projected_nodes)
    template_ref = _mapping_at(raw, "spec", "workflowTemplateRef")
    return WorkflowProviderDetail(
        phase=phase,
        started_at=_text_at(raw, "status", "startedAt"),
        finished_at=_text_at(raw, "status", "finishedAt"),
        progress=_text_at(raw, "status", "progress"),
        estimated_duration_seconds=_int_at(raw, "status", "estimatedDuration"),
        workflow_template_ref=_workflow_template_reference(
            template_ref,
            _text_at(raw, "metadata", "namespace"),
        ),
        argument_names=[
            name
            for item in _mapping_items_at(raw, "spec", "arguments", "parameters")
            if (name := _text(item.get("name"))) is not None
        ],
        resource_durations=[
            ProviderKeyValue(key=key, value=value)
            for key, raw_value in sorted(_mapping_at(raw, "status", "resourcesDuration").items())[
                :MAX_COLLECTION_ITEMS
            ]
            if (value := _scalar_text(raw_value)) is not None
        ],
        execution_nodes=projected_nodes,
        observed_node_count=len(raw_nodes),
        projected_node_count=len(projected_nodes),
        truncated=len(raw_nodes) > len(projected_nodes),
        problem_summaries=problem_summaries,
        conditions=_conditions(raw),
    )


def _workflow_execution_nodes(
    raw_nodes: Mapping[str, Any],
) -> list[WorkflowExecutionNodeDetail]:
    nodes = {
        node_id: _mapping(raw_node)
        for node_id, raw_node in sorted(raw_nodes.items())[:MAX_WORKFLOW_NODES]
        if _text(node_id) is not None and isinstance(raw_node, Mapping)
    }
    children: dict[str, list[str]] = {}
    parents: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for node_id, node in nodes.items():
        related = [
            child
            for child in [
                *_text_items(node.get("children")),
                *_text_items(node.get("outboundNodes")),
            ]
            if child in nodes
        ]
        children[node_id] = list(dict.fromkeys(related))
        for child in children[node_id]:
            parents[child].append(node_id)
    depths: dict[str, int] = {}
    ordered_ids: list[str] = []

    def visit(node_id: str, depth: int, path: frozenset[str]) -> None:
        if node_id in path:
            return
        bounded_depth = min(depth, 20)
        previous = depths.get(node_id)
        if previous is not None and previous <= bounded_depth:
            return
        if previous is None:
            ordered_ids.append(node_id)
        depths[node_id] = bounded_depth
        for child in children[node_id]:
            visit(child, bounded_depth + 1, path | {node_id})

    for node_id in nodes:
        if not parents[node_id]:
            visit(node_id, 0, frozenset())
    for node_id in nodes:
        if node_id not in depths:
            visit(node_id, 0, frozenset())

    def project(node_id: str) -> WorkflowExecutionNodeDetail:
        node = nodes[node_id]
        return WorkflowExecutionNodeDetail(
            id=node_id,
            label=_text(node.get("displayName")) or _text(node.get("name")) or node_id,
            node_type=_text(node.get("type")) or "Unknown",
            phase=_text(node.get("phase"))
            or ("Skipped" if _text(node.get("type")) == "Skipped" else "Pending"),
            depth=depths.get(node_id, 0),
            started_at=_text(node.get("startedAt")),
            finished_at=_text(node.get("finishedAt")),
            message=_bounded_text(node.get("message"), 300),
            template_ref=_workflow_template_reference(
                _mapping(node.get("templateRef")),
                None,
            ),
        )

    return [project(node_id) for node_id in ordered_ids]


def _workflow_template_reference(
    value: Mapping[str, Any], namespace: str | None
) -> ProviderNamedReference | None:
    name = _text(value.get("name")) or _text(value.get("template"))
    if name is None:
        return None
    cluster_scope = _bool(value.get("clusterScope")) is True
    return ProviderNamedReference(
        api_version=ARGO_GROUP,
        kind="ClusterWorkflowTemplate" if cluster_scope else "WorkflowTemplate",
        namespace=None if cluster_scope else namespace,
        name=name,
    )


def _workflow_problem_summaries(
    phase: str,
    raw: Mapping[str, Any],
    nodes: list[WorkflowExecutionNodeDetail],
) -> list[str]:
    if phase not in {"Failed", "Error"}:
        return []
    result: list[str] = []
    top_level = _bounded_text(
        _text_at(raw, "status", "message")
        or ("Workflow failed" if phase == "Failed" else "Workflow error"),
        300,
    )
    if top_level is not None:
        result.append(top_level)
    for node in nodes:
        if node.phase not in {"Failed", "Error"}:
            continue
        summary = f"{node.label}: {node.message}" if node.message else f"{node.label} failed"
        bounded = _bounded_text(summary, 300)
        if bounded is not None and bounded not in result:
            result.append(bounded)
    return result[:MAX_COLLECTION_ITEMS]


def _gateway_class(raw: Mapping[str, Any]) -> GatewayClassProviderDetail:
    return GatewayClassProviderDetail(
        controller_name=_text_at(raw, "spec", "controllerName"),
        description=_text_at(raw, "spec", "description"),
        accepted=_condition_truth(raw, "Accepted"),
        parameters_ref=_named_reference_at(raw, "spec", "parametersRef"),
        conditions=_conditions(raw),
    )


def _gcp_machine(raw: Mapping[str, Any]) -> GcpMachineProviderDetail:
    return GcpMachineProviderDetail(
        ready=_condition_truth(raw, "Ready"),
        instance_type=_text_at(raw, "spec", "instanceType"),
        zone=_text_at(raw, "spec", "zone") or _text_at(raw, "spec", "failureDomain"),
        instance_id=_text_at(raw, "status", "instanceID") or _text_at(raw, "spec", "providerID"),
        image=_text_at(raw, "spec", "image"),
        additional_disks=[
            GcpAdditionalDiskDetail(
                device_type=_text(item.get("deviceType")),
                size_gb=_int(item.get("size")),
            )
            for item in _mapping_items_at(raw, "spec", "additionalDisks")
        ],
        conditions=_conditions(raw),
    )


def _gcp_managed_control_plane(
    raw: Mapping[str, Any],
) -> GcpManagedControlPlaneProviderDetail:
    return GcpManagedControlPlaneProviderDetail(
        ready=_condition_truth(raw, "Ready"),
        cluster_name=_text_at(raw, "spec", "clusterName") or _text_at(raw, "metadata", "name"),
        project=_text_at(raw, "spec", "project"),
        location=_text_at(raw, "spec", "location"),
        version=_text_at(raw, "status", "version") or _text_at(raw, "spec", "version"),
        release_channel=_text_at(raw, "spec", "releaseChannel"),
        autopilot=_bool_at(raw, "spec", "enableAutopilot"),
        endpoint=_first_endpoint(
            _mapping_at(raw, "spec", "endpoint"),
            _mapping_at(raw, "spec", "controlPlaneEndpoint"),
        ),
        pod_cidr=_text_at(raw, "spec", "clusterNetwork", "pod", "cidrBlock"),
        service_cidr=_text_at(raw, "spec", "clusterNetwork", "service", "cidrBlock"),
        ip_aliases=_bool_at(raw, "spec", "clusterNetwork", "useIPAliases"),
        logging_service=_text_at(raw, "spec", "loggingService"),
        monitoring_service=_text_at(raw, "spec", "monitoringService"),
        authorized_networks=[
            GcpAuthorizedNetworkDetail(
                name=_text(item.get("display_name")),
                cidr=cidr,
            )
            for item in _mapping_items_at(
                raw,
                "spec",
                "master_authorized_networks_config",
                "cidr_blocks",
            )
            if (cidr := _text(item.get("cidr_block"))) is not None
        ],
        conditions=_conditions(raw),
    )


def _gcp_managed_machine_pool(
    raw: Mapping[str, Any],
) -> GcpManagedMachinePoolProviderDetail:
    management = _mapping_at(raw, "spec", "management")
    return GcpManagedMachinePoolProviderDetail(
        ready=_condition_truth(raw, "Ready"),
        node_pool_name=_text_at(raw, "spec", "nodePoolName") or _text_at(raw, "metadata", "name"),
        machine_type=_text_at(raw, "spec", "machineType") or _text_at(raw, "spec", "instanceType"),
        disk_type=_text_at(raw, "spec", "diskType"),
        disk_size_gb=_first_present_int(
            _int_at(raw, "spec", "diskSizeGb"),
            _int_at(raw, "spec", "diskSizeGB"),
        ),
        image_type=_text_at(raw, "spec", "imageType"),
        max_pods_per_node=_int_at(raw, "spec", "maxPodsPerNode"),
        autoscaling_enabled=_bool_at(raw, "spec", "scaling", "enableAutoscaling"),
        scaling=ProviderScaling(
            minimum=_int_at(raw, "spec", "scaling", "minCount"),
            maximum=_int_at(raw, "spec", "scaling", "maxCount"),
            current=_int_at(raw, "status", "replicas"),
        ),
        auto_repair=_bool(management.get("autoRepair")),
        auto_upgrade=_bool(management.get("autoUpgrade")),
        node_locations=_text_items_at(raw, "spec", "nodeLocations"),
        labels=_key_values_at(raw, "spec", "kubernetesLabels"),
        taints=[
            ProviderTaint(
                key=key,
                value=_text(item.get("value")),
                effect=_text(item.get("effect")),
            )
            for item in _mapping_items_at(raw, "spec", "kubernetesTaints")
            if (key := _text(item.get("key"))) is not None
        ],
        conditions=_conditions(raw),
    )


def _grpc_route(raw: Mapping[str, Any]) -> GrpcRouteProviderDetail:
    parent_statuses = _gateway_route_parent_statuses(raw)
    return GrpcRouteProviderDetail(
        hostnames=_text_items_at(raw, "spec", "hostnames"),
        parent_refs=_gateway_route_parent_refs(raw),
        rules=_gateway_route_rules(raw, grpc=True),
        parent_statuses=parent_statuses,
        conditions=parent_statuses[0].conditions if parent_statuses else [],
    )


def _http_route(raw: Mapping[str, Any]) -> HttpRouteProviderDetail:
    parent_statuses = _gateway_route_parent_statuses(raw)
    return HttpRouteProviderDetail(
        hostnames=_text_items_at(raw, "spec", "hostnames"),
        parent_refs=_gateway_route_parent_refs(raw),
        rules=_gateway_route_rules(raw, grpc=False),
        parent_statuses=parent_statuses,
        conditions=parent_statuses[0].conditions if parent_statuses else [],
    )


def _gateway_route_parent_refs(raw: Mapping[str, Any]) -> list[ProviderNamedReference]:
    namespace = _text_at(raw, "metadata", "namespace")
    return [
        reference
        for item in _mapping_items_at(raw, "spec", "parentRefs")[:MAX_ROUTE_ITEMS]
        if (
            reference := _route_reference(
                item,
                default_namespace=namespace,
            )
        )
        is not None
    ]


def _gateway_route_rules(raw: Mapping[str, Any], *, grpc: bool) -> list[GatewayRouteRuleDetail]:
    namespace = _text_at(raw, "metadata", "namespace")
    return [
        GatewayRouteRuleDetail(
            matches=[
                _gateway_route_match(item, grpc=grpc)
                for item in _mapping_items(rule.get("matches"))[:MAX_ROUTE_ITEMS]
            ],
            backends=[
                backend
                for item in _mapping_items(rule.get("backendRefs"))[:MAX_ROUTE_ITEMS]
                if (
                    backend := _gateway_route_backend(
                        item,
                        default_namespace=namespace,
                    )
                )
                is not None
            ],
            filters=[
                GatewayRouteFilterDetail(
                    type=filter_type,
                    summary=_gateway_route_filter_summary(item, filter_type),
                )
                for item in _mapping_items(rule.get("filters"))[:MAX_ROUTE_ITEMS]
                if (filter_type := _text(item.get("type"))) is not None
            ],
        )
        for rule in _mapping_items_at(raw, "spec", "rules")[:MAX_ROUTE_RULES]
    ]


def _gateway_route_match(item: Mapping[str, Any], *, grpc: bool) -> GatewayRouteMatchDetail:
    method = _mapping(item.get("method"))
    path = _mapping(item.get("path"))
    return GatewayRouteMatchDetail(
        method=None if grpc else _text(item.get("method")),
        path_type=None if grpc else _text(path.get("type")),
        path_value=None if grpc else _text(path.get("value")),
        grpc_type=_text(method.get("type")) if grpc else None,
        grpc_service=_text(method.get("service")) if grpc else None,
        grpc_method=_text(method.get("method")) if grpc else None,
        headers=_named_value_items(item.get("headers")),
        query_params=[] if grpc else _named_value_items(item.get("queryParams")),
    )


def _gateway_route_backend(
    item: Mapping[str, Any], *, default_namespace: str | None
) -> GatewayRouteBackendDetail | None:
    reference = _route_reference(item, default_namespace=default_namespace)
    if reference is None:
        return None
    return GatewayRouteBackendDetail(
        reference=reference,
        port=_int(item.get("port")),
        weight=_int(item.get("weight")),
    )


def _gateway_route_parent_statuses(
    raw: Mapping[str, Any],
) -> list[GatewayRouteParentStatusDetail]:
    namespace = _text_at(raw, "metadata", "namespace")
    result: list[GatewayRouteParentStatusDetail] = []
    for item in _mapping_items_at(raw, "status", "parents")[:MAX_ROUTE_ITEMS]:
        conditions = _condition_items(item.get("conditions"))
        result.append(
            GatewayRouteParentStatusDetail(
                reference=_route_reference(
                    _mapping(item.get("parentRef")),
                    default_namespace=namespace,
                ),
                section_name=_text_at(item, "parentRef", "sectionName"),
                accepted=_condition_truth_from(conditions, "Accepted"),
                resolved_refs=_condition_truth_from(conditions, "ResolvedRefs"),
                conditions=conditions,
            )
        )
    return result


def _route_reference(
    item: Mapping[str, Any], *, default_namespace: str | None
) -> ProviderNamedReference | None:
    name = _text(item.get("name"))
    if name is None:
        return None
    return ProviderNamedReference(
        api_version=_text(item.get("group")),
        kind=_text(item.get("kind")),
        namespace=_text(item.get("namespace")) or default_namespace,
        name=name,
    )


def _named_value_items(value: object) -> list[ProviderKeyValue]:
    return [
        ProviderKeyValue(key=name, value=item_value)
        for item in _mapping_items(value)[:MAX_ROUTE_ITEMS]
        if (name := _text(item.get("name"))) is not None
        and _normalized_route_value_name(name) not in SENSITIVE_ROUTE_VALUE_NAMES
        and (item_value := _text(item.get("value"))) is not None
    ]


def _normalized_route_value_name(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _gateway_route_filter_summary(item: Mapping[str, Any], filter_type: str) -> str | None:
    if filter_type == "RequestHeaderModifier":
        return _header_modifier_summary(_mapping(item.get("requestHeaderModifier")))
    if filter_type == "ResponseHeaderModifier":
        return _header_modifier_summary(_mapping(item.get("responseHeaderModifier")))
    if filter_type == "RequestRedirect":
        redirect = _mapping(item.get("requestRedirect"))
        return _joined_text(
            redirect.get("scheme"),
            redirect.get("hostname"),
            redirect.get("port"),
            redirect.get("statusCode"),
        )
    if filter_type == "URLRewrite":
        rewrite = _mapping(item.get("urlRewrite"))
        return _joined_text(
            rewrite.get("hostname"),
            _value_at(rewrite, "path", "replacePrefixMatch"),
        )
    if filter_type == "RequestMirror":
        mirror = _mapping_at(item, "requestMirror", "backendRef")
        return _joined_text(mirror.get("name"), mirror.get("port"))
    return None


def _header_modifier_summary(modifier: Mapping[str, Any]) -> str | None:
    parts: list[str] = []
    for operation in ("set", "add"):
        names = [
            name
            for item in _mapping_items(modifier.get(operation))[:MAX_ROUTE_ITEMS]
            if (name := _text(item.get("name"))) is not None
        ]
        if names:
            parts.append(f"{operation}: {', '.join(names)}")
    removed = _text_items(modifier.get("remove"))[:MAX_ROUTE_ITEMS]
    if removed:
        parts.append(f"remove: {', '.join(removed)}")
    return "; ".join(parts) if parts else None


def _job(raw: Mapping[str, Any]) -> JobProviderDetail:
    conditions = _conditions(raw)
    complete = _condition_by_type(conditions, "Complete")
    failed = _condition_by_type(conditions, "Failed")
    suspended = _bool_at(raw, "spec", "suspend")
    active = _int_at(raw, "status", "active")
    state: Literal["completed", "failed", "suspended", "running", "pending"] = (
        "completed"
        if complete is not None and complete.status == "True"
        else "failed"
        if failed is not None and failed.status == "True"
        else "suspended"
        if suspended is True
        else "running"
        if (active or 0) > 0 or _text_at(raw, "status", "startTime") is not None
        else "pending"
    )
    terminal = failed if state == "failed" else complete if state == "completed" else None
    return JobProviderDetail(
        state=state,
        succeeded=_int_at(raw, "status", "succeeded"),
        failed=_int_at(raw, "status", "failed"),
        active=active,
        completions=_int_at(raw, "spec", "completions"),
        parallelism=_int_at(raw, "spec", "parallelism"),
        backoff_limit=_int_at(raw, "spec", "backoffLimit"),
        active_deadline_seconds=_int_at(raw, "spec", "activeDeadlineSeconds"),
        ttl_seconds_after_finished=_int_at(raw, "spec", "ttlSecondsAfterFinished"),
        suspended=suspended,
        start_time=_text_at(raw, "status", "startTime"),
        completion_time=_text_at(raw, "status", "completionTime")
        or (terminal.last_transition_time if terminal is not None else None),
        terminal_reason=terminal.reason if terminal is not None else None,
        terminal_message=terminal.message if terminal is not None else None,
        conditions=conditions,
    )


def _karpenter_ec2_node_class(
    raw: Mapping[str, Any],
) -> KarpenterEc2NodeClassProviderDetail:
    metadata_options = _mapping_at(raw, "spec", "metadataOptions")
    return KarpenterEc2NodeClassProviderDetail(
        ready=_condition_truth(raw, "Ready"),
        role=_text_at(raw, "spec", "role"),
        instance_profile=_text_at(raw, "status", "instanceProfile"),
        ami_family=_text_at(raw, "spec", "amiFamily"),
        ami_selector_terms=_karpenter_selector_terms(raw, "spec", "amiSelectorTerms"),
        block_devices=[
            KarpenterBlockDeviceDetail(
                device_name=_text(item.get("deviceName")),
                volume_type=_text_at(item, "ebs", "volumeType"),
                volume_size=_scalar_text_at(item, "ebs", "volumeSize"),
                iops=_int_at(item, "ebs", "iops"),
                throughput=_int_at(item, "ebs", "throughput"),
                encrypted=_bool_at(item, "ebs", "encrypted"),
                delete_on_termination=_bool_at(item, "ebs", "deleteOnTermination"),
            )
            for item in _mapping_items_at(raw, "spec", "blockDeviceMappings")
        ],
        subnet_selector_terms=_karpenter_selector_terms(raw, "spec", "subnetSelectorTerms"),
        security_group_selector_terms=_karpenter_selector_terms(
            raw, "spec", "securityGroupSelectorTerms"
        ),
        metadata_options=(
            KarpenterMetadataOptionsDetail(
                http_tokens=_text(metadata_options.get("httpTokens")),
                http_put_response_hop_limit=_int(metadata_options.get("httpPutResponseHopLimit")),
                http_endpoint=_text(metadata_options.get("httpEndpoint")),
            )
            if metadata_options
            else None
        ),
        resolved_amis=[
            KarpenterResolvedAmiDetail(
                id=ami_id,
                name=_text(item.get("name")),
                requirements=_provider_requirements(item.get("requirements")),
            )
            for item in _mapping_items_at(raw, "status", "amis")
            if (ami_id := _text(item.get("id"))) is not None
        ],
        resolved_subnets=_karpenter_resolved_networks(raw, "status", "subnets"),
        resolved_security_groups=_karpenter_resolved_networks(raw, "status", "securityGroups"),
        tags=_safe_key_values_at(raw, "spec", "tags"),
        conditions=_conditions(raw),
    )


def _karpenter_node_claim(raw: Mapping[str, Any]) -> KarpenterNodeClaimProviderDetail:
    conditions = _conditions(raw)
    ready = _condition_by_type(conditions, "Ready")
    launched = _condition_by_type(conditions, "Launched")
    registered = _condition_by_type(conditions, "Registered")
    initialized = _condition_by_type(conditions, "Initialized")
    state: Literal[
        "ready",
        "registered",
        "launched",
        "initialized",
        "not-ready",
        "pending",
        "unknown",
    ] = (
        "ready"
        if ready is not None and ready.status == "True"
        else "registered"
        if launched is not None
        and launched.status == "True"
        and registered is not None
        and registered.status == "True"
        else "launched"
        if launched is not None and launched.status == "True"
        else "initialized"
        if initialized is not None and initialized.status == "True"
        else "not-ready"
        if ready is not None and ready.status == "False"
        else "unknown"
        if ready is not None and ready.status == "Unknown"
        else "pending"
    )
    requirements = _provider_requirements(_value_at(raw, "spec", "requirements"))
    instance_type = (
        _text_at(raw, "status", "instanceType")
        or _text_at(raw, "metadata", "labels", "node.kubernetes.io/instance-type")
        or _requirement_first_value(requirements, "node.kubernetes.io/instance-type")
    )
    return KarpenterNodeClaimProviderDetail(
        state=state,
        instance_type=instance_type,
        capacity_type=_text_at(raw, "metadata", "labels", "karpenter.sh/capacity-type"),
        node_name=_text_at(raw, "status", "nodeName"),
        zone=_text_at(raw, "metadata", "labels", "topology.kubernetes.io/zone"),
        architecture=_text_at(raw, "metadata", "labels", "kubernetes.io/arch"),
        node_pool=_text_at(raw, "metadata", "labels", "karpenter.sh/nodepool"),
        node_class_ref=_named_reference_at(raw, "spec", "nodeClassRef"),
        image_id=_text_at(raw, "status", "imageID"),
        expire_after=_text_at(raw, "spec", "expireAfter"),
        capacity=KarpenterCapacityDetail(
            cpu=_scalar_text_at(raw, "status", "capacity", "cpu"),
            memory=_scalar_text_at(raw, "status", "capacity", "memory"),
            pods=_scalar_text_at(raw, "status", "capacity", "pods"),
            ephemeral_storage=_scalar_text_at(raw, "status", "capacity", "ephemeral-storage"),
        ),
        requirements=requirements,
        conditions=conditions,
    )


def _karpenter_node_pool(raw: Mapping[str, Any]) -> KarpenterNodePoolProviderDetail:
    expire_after = _text_at(raw, "spec", "disruption", "expireAfter") or _text_at(
        raw, "spec", "template", "spec", "expireAfter"
    )
    return KarpenterNodePoolProviderDetail(
        ready=_condition_truth(raw, "Ready"),
        node_class_ref=_named_reference_at(raw, "spec", "template", "spec", "nodeClassRef"),
        limit_cpu=_scalar_text_at(raw, "spec", "limits", "cpu"),
        limit_memory=_scalar_text_at(raw, "spec", "limits", "memory"),
        weight=_int_at(raw, "spec", "weight"),
        current_cpu=_scalar_text_at(raw, "status", "resources", "cpu"),
        current_memory=_scalar_text_at(raw, "status", "resources", "memory"),
        consolidation_policy=_text_at(raw, "spec", "disruption", "consolidationPolicy"),
        consolidate_after=_text_at(raw, "spec", "disruption", "consolidateAfter"),
        expire_after=expire_after,
        disruption_budgets=[
            KarpenterDisruptionBudgetDetail(
                nodes=_scalar_text(item.get("nodes")),
                schedule=_text(item.get("schedule")),
                duration=_text(item.get("duration")),
            )
            for item in _mapping_items_at(raw, "spec", "disruption", "budgets")
        ],
        template_labels=_safe_key_values_at(raw, "spec", "template", "metadata", "labels"),
        template_taints=_provider_taints_at(raw, "spec", "template", "spec", "taints"),
        startup_taints=_provider_taints_at(raw, "spec", "template", "spec", "startupTaints"),
        requirements=_provider_requirements(
            _value_at(raw, "spec", "template", "spec", "requirements")
        ),
        conditions=_conditions(raw),
    )


def _keda_scaled_object(raw: Mapping[str, Any]) -> KedaScaledObjectProviderDetail:
    conditions = _conditions(raw)
    annotations = _mapping_at(raw, "metadata", "annotations")
    paused = (
        _text(annotations.get("autoscaling.keda.sh/paused")) == "true"
        or "autoscaling.keda.sh/paused-replicas" in annotations
        or _condition_truth_from(conditions, "Paused") is True
    )
    fallback = _condition_truth_from(conditions, "Fallback")
    ready = _condition_truth_from(conditions, "Ready")
    active = _condition_truth_from(conditions, "Active")
    state: Literal["paused", "fallback", "not-ready", "active", "idle", "ready", "unknown"] = (
        "paused"
        if paused
        else "fallback"
        if fallback is True
        else "not-ready"
        if ready is False
        else "active"
        if active is True
        else "idle"
        if active is False
        else "ready"
        if ready is True
        else "unknown"
    )
    namespace = _text_at(raw, "metadata", "namespace")
    scale_up = _mapping_at(
        raw,
        "spec",
        "advanced",
        "horizontalPodAutoscalerConfig",
        "behavior",
        "scaleUp",
    )
    scale_down = _mapping_at(
        raw,
        "spec",
        "advanced",
        "horizontalPodAutoscalerConfig",
        "behavior",
        "scaleDown",
    )
    return KedaScaledObjectProviderDetail(
        state=state,
        target_ref=_named_reference_with_namespace(
            _mapping_at(raw, "spec", "scaleTargetRef"), namespace
        ),
        scaling=ProviderScaling(
            minimum=_int_at(raw, "spec", "minReplicaCount"),
            maximum=_int_at(raw, "spec", "maxReplicaCount"),
            current=None,
        ),
        idle_replicas=_int_at(raw, "spec", "idleReplicaCount"),
        polling_interval_seconds=_int_at(raw, "spec", "pollingInterval"),
        cooldown_period_seconds=_int_at(raw, "spec", "cooldownPeriod"),
        hpa_name=_text_at(raw, "status", "hpaName"),
        last_active_time=_text_at(raw, "status", "lastActiveTime"),
        fallback_failure_threshold=_int_at(raw, "spec", "fallback", "failureThreshold"),
        fallback_replicas=_int_at(raw, "spec", "fallback", "replicas"),
        restore_original_replicas=_bool_at(
            raw, "spec", "advanced", "restoreToOriginalReplicaCount"
        ),
        scale_up_stabilization_seconds=_int(scale_up.get("stabilizationWindowSeconds")),
        scale_down_stabilization_seconds=_int(scale_down.get("stabilizationWindowSeconds")),
        scaling_policies=[
            *_keda_scaling_policies(scale_up, "up"),
            *_keda_scaling_policies(scale_down, "down"),
        ],
        triggers=_keda_triggers(raw, namespace),
        conditions=conditions,
    )


def _keda_scaled_job(raw: Mapping[str, Any]) -> KedaScaledJobProviderDetail:
    conditions = _conditions(raw)
    ready = _condition_truth_from(conditions, "Ready")
    active = _condition_truth_from(conditions, "Active")
    state: Literal["not-ready", "active", "idle", "ready", "unknown"] = (
        "not-ready"
        if ready is False
        else "active"
        if active is True
        else "idle"
        if active is False
        else "ready"
        if ready is True
        else "unknown"
    )
    return KedaScaledJobProviderDetail(
        state=state,
        job_target_name=_text_at(raw, "spec", "jobTargetRef", "name"),
        strategy=_text_at(raw, "spec", "scalingStrategy", "strategy"),
        polling_interval_seconds=_int_at(raw, "spec", "pollingInterval"),
        successful_history_limit=_int_at(raw, "spec", "successfulJobsHistoryLimit"),
        failed_history_limit=_int_at(raw, "spec", "failedJobsHistoryLimit"),
        minimum_replicas=_int_at(raw, "spec", "minReplicaCount"),
        maximum_replicas=_int_at(raw, "spec", "maxReplicaCount"),
        triggers=_keda_triggers(raw, _text_at(raw, "metadata", "namespace")),
        conditions=conditions,
    )


def _sbom_report(raw: Mapping[str, Any]) -> SbomReportProviderDetail:
    raw_components = _value_at(raw, "report", "components", "components")
    observed_components = raw_components if isinstance(raw_components, list) else []
    components: list[SbomComponentDetail] = []
    for item in _bounded_mapping_items(raw_components, MAX_SBOM_COMPONENTS):
        name = _text(item.get("name"))
        if name is None:
            continue
        package_url, qualifiers_redacted = _safe_package_url(item.get("purl"))
        components.append(
            SbomComponentDetail(
                name=name,
                version=_text(item.get("version")),
                type=_text(item.get("type")),
                package_url=package_url,
                package_url_qualifiers_redacted=qualifiers_redacted,
                license=_sbom_license(item),
            )
        )
    observed_count = len(observed_components)
    return SbomReportProviderDetail(
        container_name=_text_at(raw, "metadata", "labels", "trivy-operator.container.name"),
        image=_trivy_image(raw),
        bom_format=_text_at(raw, "report", "components", "bomFormat"),
        spec_version=_text_at(raw, "report", "components", "specVersion"),
        component_count=_first_present_int(
            _nonnegative_safe_int_at(raw, "report", "summary", "componentsCount"),
            observed_count,
        )
        or 0,
        dependency_count=_nonnegative_safe_int_at(raw, "report", "summary", "dependenciesCount")
        or 0,
        observed_component_count=observed_count,
        projected_component_count=len(components),
        truncated=len(components) < observed_count,
        scanner_name=_text_at(raw, "report", "scanner", "name"),
        scanner_version=_text_at(raw, "report", "scanner", "version"),
        scanned_at=_text_at(raw, "report", "updateTimestamp"),
        components=components,
        conditions=_conditions(raw),
    )


def _vulnerability_report(raw: Mapping[str, Any]) -> VulnerabilityReportProviderDetail:
    raw_vulnerabilities = _value_at(raw, "report", "vulnerabilities")
    observed_vulnerabilities = raw_vulnerabilities if isinstance(raw_vulnerabilities, list) else []
    calculated_counts = {
        "CRITICAL": 0,
        "HIGH": 0,
        "MEDIUM": 0,
        "LOW": 0,
        "UNKNOWN": 0,
    }
    for item in observed_vulnerabilities:
        if isinstance(item, Mapping):
            calculated_counts[_vulnerability_severity(item.get("severity"))] += 1

    vulnerabilities: list[VulnerabilityFindingDetail] = []
    for item in _bounded_mapping_items(raw_vulnerabilities, MAX_VULNERABILITIES):
        vulnerability_id = _text(item.get("vulnerabilityID"))
        if vulnerability_id is None:
            continue
        vulnerabilities.append(
            VulnerabilityFindingDetail(
                vulnerability_id=vulnerability_id,
                severity=_vulnerability_severity(item.get("severity")),
                score=_bounded_score(item.get("score")),
                package=_text(item.get("resource")),
                installed_version=_text(item.get("installedVersion")),
                fixed_version=_text(item.get("fixedVersion")),
                primary_link=_safe_external_http_url(item.get("primaryLink")),
            )
        )

    observed_count = len(observed_vulnerabilities)
    os_detail = _mapping_at(raw, "report", "os")
    return VulnerabilityReportProviderDetail(
        container_name=_text_at(raw, "metadata", "labels", "trivy-operator.container.name"),
        image=_trivy_image(raw),
        os_family=_text(os_detail.get("family")),
        os_name=_text(os_detail.get("name")),
        os_end_of_service_life=_bool(os_detail.get("eosl")),
        scanner_name=_text_at(raw, "report", "scanner", "name"),
        scanner_version=_text_at(raw, "report", "scanner", "version"),
        scanned_at=_text_at(raw, "report", "updateTimestamp"),
        severity=SecuritySeveritySummaryDetail(
            critical=_reported_or_calculated_count(
                raw, "criticalCount", calculated_counts["CRITICAL"]
            ),
            high=_reported_or_calculated_count(raw, "highCount", calculated_counts["HIGH"]),
            medium=_reported_or_calculated_count(raw, "mediumCount", calculated_counts["MEDIUM"]),
            low=_reported_or_calculated_count(raw, "lowCount", calculated_counts["LOW"]),
            unknown=_reported_or_calculated_count(
                raw, "unknownCount", calculated_counts["UNKNOWN"]
            ),
        ),
        observed_vulnerability_count=observed_count,
        projected_vulnerability_count=len(vulnerabilities),
        truncated=len(vulnerabilities) < observed_count,
        vulnerabilities=vulnerabilities,
        conditions=_conditions(raw),
    )


def _prometheus_rule(raw: Mapping[str, Any]) -> PrometheusRuleProviderDetail:
    raw_groups = _value_at(raw, "spec", "groups")
    observed_groups = raw_groups if isinstance(raw_groups, list) else []
    total_rules = 0
    total_alerts = 0
    total_recordings = 0
    for raw_group in observed_groups:
        if not isinstance(raw_group, Mapping):
            continue
        raw_rules = raw_group.get("rules")
        if not isinstance(raw_rules, list):
            continue
        total_rules += len(raw_rules)
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, Mapping):
                continue
            if _text(raw_rule.get("alert")) is not None:
                total_alerts += 1
            elif _text(raw_rule.get("record")) is not None:
                total_recordings += 1
    groups: list[PrometheusRuleGroupDetail] = []
    remaining = MAX_PROMETHEUS_RULES
    for group in _mapping_items_at(raw, "spec", "groups")[:MAX_PROMETHEUS_GROUPS]:
        group_name = _text(group.get("name"))
        if group_name is None or remaining <= 0:
            continue
        raw_group_rules = group.get("rules")
        observed_group_rules = raw_group_rules if isinstance(raw_group_rules, list) else []
        rules: list[PrometheusRuleEntryDetail] = []
        for item in _mapping_items(group.get("rules"))[:remaining]:
            alert = _text(item.get("alert"))
            recording = _text(item.get("record"))
            name = alert or recording
            expression = _scalar_text(item.get("expr"))
            if name is None or expression is None:
                continue
            labels = _safe_key_values(_mapping(item.get("labels")))
            if alert is not None:
                rules.append(
                    PrometheusRuleEntryDetail(
                        type="alert",
                        name=name,
                        expression=expression,
                        duration=_text(item.get("for")),
                        severity=_text_at(item, "labels", "severity"),
                        summary=_text_at(item, "annotations", "summary"),
                        description=_text_at(item, "annotations", "description"),
                        labels=labels,
                    )
                )
            else:
                rules.append(
                    PrometheusRuleEntryDetail(
                        type="recording",
                        name=name,
                        expression=expression,
                        labels=labels,
                    )
                )
        remaining -= len(rules)
        groups.append(
            PrometheusRuleGroupDetail(
                name=group_name,
                interval=_text(group.get("interval")),
                rule_count=len(observed_group_rules),
                alert_count=sum(
                    isinstance(item, Mapping) and _text(item.get("alert")) is not None
                    for item in observed_group_rules
                ),
                recording_count=sum(
                    isinstance(item, Mapping) and _text(item.get("record")) is not None
                    for item in observed_group_rules
                ),
                rules=rules,
            )
        )
    projected_rules = sum(len(group.rules) for group in groups)
    return PrometheusRuleProviderDetail(
        group_count=len(observed_groups),
        total_rules=total_rules,
        total_alerts=total_alerts,
        total_recordings=total_recordings,
        projected_rules=projected_rules,
        truncated=len(groups) < len(observed_groups) or projected_rules < total_rules,
        groups=groups,
        conditions=_conditions(raw),
    )


def _simple_route(
    raw: Mapping[str, Any], *, tls: bool
) -> TcpRouteProviderDetail | TlsRouteProviderDetail:
    parent_statuses = _gateway_route_parent_statuses(raw)
    detail_type = TlsRouteProviderDetail if tls else TcpRouteProviderDetail
    return detail_type(
        hostnames=_text_items_at(raw, "spec", "hostnames") if tls else [],
        parent_refs=_gateway_route_parent_refs(raw),
        rules=_gateway_simple_route_rules(raw),
        parent_statuses=parent_statuses,
        conditions=parent_statuses[0].conditions if parent_statuses else [],
    )


def _tcp_route(raw: Mapping[str, Any]) -> TcpRouteProviderDetail:
    return _simple_route(raw, tls=False)


def _tls_route(raw: Mapping[str, Any]) -> TlsRouteProviderDetail:
    return _simple_route(raw, tls=True)


def _gateway_simple_route_rules(raw: Mapping[str, Any]) -> list[GatewayRouteRuleDetail]:
    namespace = _text_at(raw, "metadata", "namespace")
    return [
        GatewayRouteRuleDetail(
            backends=[
                backend
                for item in _mapping_items(rule.get("backendRefs"))[:MAX_ROUTE_ITEMS]
                if (
                    backend := _gateway_route_backend(
                        item,
                        default_namespace=namespace,
                    )
                )
                is not None
            ]
        )
        for rule in _mapping_items_at(raw, "spec", "rules")[:MAX_ROUTE_RULES]
    ]


def _karpenter_selector_terms(
    raw: Mapping[str, Any], *path: str
) -> list[KarpenterSelectorTermDetail]:
    return [
        KarpenterSelectorTermDetail(
            id=_text(item.get("id")),
            name=_text(item.get("name")),
            alias=_text(item.get("alias")),
            owner=_text(item.get("owner")),
            tags=_safe_key_values(_mapping(item.get("tags"))),
        )
        for item in _mapping_items_at(raw, *path)
    ]


def _karpenter_resolved_networks(
    raw: Mapping[str, Any], *path: str
) -> list[KarpenterResolvedNetworkDetail]:
    return [
        KarpenterResolvedNetworkDetail(
            id=resource_id,
            name=_text(item.get("name")),
            zone=_text(item.get("zone")),
        )
        for item in _mapping_items_at(raw, *path)
        if (resource_id := _text(item.get("id"))) is not None
    ]


def _provider_requirements(value: object) -> list[ProviderRequirementDetail]:
    return [
        ProviderRequirementDetail(
            key=key,
            operator=_text(item.get("operator")),
            values=_text_items(item.get("values")),
            min_values=_int(item.get("minValues")),
        )
        for item in _mapping_items(value)
        if (key := _text(item.get("key"))) is not None
    ]


def _requirement_first_value(requirements: list[ProviderRequirementDetail], key: str) -> str | None:
    requirement = next((item for item in requirements if item.key == key), None)
    return requirement.values[0] if requirement is not None and requirement.values else None


def _provider_taints_at(raw: Mapping[str, Any], *path: str) -> list[ProviderTaint]:
    return [
        ProviderTaint(
            key=key,
            value=_text(item.get("value")),
            effect=_text(item.get("effect")),
        )
        for item in _mapping_items_at(raw, *path)
        if (key := _text(item.get("key"))) is not None
    ]


def _keda_triggers(raw: Mapping[str, Any], namespace: str | None) -> list[KedaTriggerDetail]:
    result: list[KedaTriggerDetail] = []
    for item in _mapping_items_at(raw, "spec", "triggers"):
        trigger_type = _text(item.get("type"))
        if trigger_type is None:
            continue
        metadata = _mapping(item.get("metadata"))
        safe_keys = [
            key
            for raw_key in sorted(metadata)
            if (key := _text(raw_key)) is not None and not _sensitive_dynamic_key(key)
        ][:MAX_COLLECTION_ITEMS]
        result.append(
            KedaTriggerDetail(
                type=trigger_type,
                name=_text(item.get("name")),
                authentication_ref=_named_reference_with_namespace(
                    _mapping(item.get("authenticationRef")), namespace
                ),
                metadata_keys=safe_keys,
                redacted_metadata_count=len(metadata),
            )
        )
    return result


def _keda_scaling_policies(
    behavior: Mapping[str, Any], direction: Literal["up", "down"]
) -> list[KedaScalingPolicyDetail]:
    return [
        KedaScalingPolicyDetail(
            direction=direction,
            type=_text(item.get("type")),
            value=_int(item.get("value")),
            period_seconds=_int(item.get("periodSeconds")),
        )
        for item in _mapping_items(behavior.get("policies"))
    ]


def _sbom_license(component: Mapping[str, Any]) -> str | None:
    licenses = _bounded_mapping_items(component.get("licenses"), 1)
    if not licenses:
        return None
    license_detail = _mapping(licenses[0].get("license"))
    return _text(license_detail.get("id")) or _text(license_detail.get("name"))


def _safe_package_url(value: object) -> tuple[str | None, bool]:
    package_url = _text(value)
    if package_url is None or not package_url.lower().startswith("pkg:"):
        return None, False
    if any(character.isspace() for character in package_url):
        return None, False
    qualifier_index = package_url.find("?")
    fragment_index = package_url.find("#")
    cut_indexes = [index for index in (qualifier_index, fragment_index) if index >= 0]
    if not cut_indexes:
        return package_url, False
    return package_url[: min(cut_indexes)], qualifier_index >= 0


def _trivy_image(raw: Mapping[str, Any]) -> str | None:
    repository = _safe_image_repository(_text_at(raw, "report", "artifact", "repository"))
    if repository is None:
        return None
    registry = _safe_registry_host(_text_at(raw, "report", "registry", "server"))
    tag = _safe_image_tag(_text_at(raw, "report", "artifact", "tag"))
    image = f"{registry}/{repository}" if registry is not None else repository
    return f"{image}:{tag}" if tag is not None else image


def _safe_registry_host(value: str | None) -> str | None:
    if value is None or any(character.isspace() for character in value):
        return None
    candidate = value
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return None
        candidate = parsed.netloc
    if not candidate or "@" in candidate or "/" in candidate:
        return None
    return candidate


def _safe_image_repository(value: str | None) -> str | None:
    if value is None or any(character.isspace() for character in value):
        return None
    allowed = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-")
    return value if all(character in allowed for character in value) else None


def _safe_image_tag(value: str | None) -> str | None:
    if value is None or any(character.isspace() for character in value):
        return None
    allowed = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    return value if all(character in allowed for character in value) else None


def _safe_external_http_url(value: object) -> str | None:
    url = _text(value)
    if url is None or any(character.isspace() for character in url):
        return None
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return url


def _vulnerability_severity(
    value: object,
) -> Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]:
    normalized = (_text(value) or "UNKNOWN").upper()
    if normalized in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
        return normalized
    return "UNKNOWN"


def _reported_or_calculated_count(raw: Mapping[str, Any], field: str, calculated: int) -> int:
    reported = _nonnegative_safe_int_at(raw, "report", "summary", field)
    return reported if reported is not None else calculated


def _bounded_score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    return score if isfinite(score) and 0 <= score <= 10 else None


PROVIDER_DETAIL_PROJECTORS: dict[tuple[str, str], ProviderDetailProjector] = {
    (INFRASTRUCTURE_GROUP, "AWSMachine"): _aws_machine,
    (INFRASTRUCTURE_GROUP, "AWSManagedCluster"): _aws_managed_cluster,
    (CONTROL_PLANE_GROUP, "AWSManagedControlPlane"): _aws_managed_control_plane,
    (INFRASTRUCTURE_GROUP, "AWSManagedMachinePool"): _aws_managed_machine_pool,
    (INFRASTRUCTURE_GROUP, "AzureMachine"): _azure_machine,
    (INFRASTRUCTURE_GROUP, "AzureManagedControlPlane"): _azure_managed_control_plane,
    (INFRASTRUCTURE_GROUP, "AzureManagedMachinePool"): _azure_managed_machine_pool,
    (CAPI_GROUP, "Cluster"): _capi_cluster,
    (CONTROL_PLANE_GROUP, "KubeadmControlPlane"): _capi_kubeadm_control_plane,
    (CAPI_GROUP, "MachineDeployment"): _capi_machine_deployment,
    (CAPI_GROUP, "MachineHealthCheck"): _capi_machine_health_check,
    (CAPI_GROUP, "MachinePool"): _capi_machine_pool,
    (CAPI_GROUP, "Machine"): _capi_machine,
    (CAPI_GROUP, "MachineSet"): _capi_machine_set,
    (CERT_MANAGER_GROUP, "Certificate"): _certificate,
    (CERT_MANAGER_GROUP, "CertificateRequest"): _certificate_request,
    (COMPLIANCE_GROUP, "ClusterComplianceReport"): _cluster_compliance_report,
    (ARGO_GROUP, "CronWorkflow"): _cron_workflow,
    (ARGO_GROUP, "Workflow"): _workflow,
    (EXTERNAL_SECRETS_GROUP, "ExternalSecret"): _external_secret,
    (EXTERNAL_SECRETS_GROUP, "SecretStore"): _secret_store,
    (EXTERNAL_SECRETS_GROUP, "ClusterSecretStore"): _cluster_secret_store,
    (SEALED_SECRETS_GROUP, "SealedSecret"): _sealed_secret,
    ("", "PersistentVolumeClaim"): _persistent_volume_claim,
    ("", "Secret"): _secret,
    (GATEWAY_GROUP, "GatewayClass"): _gateway_class,
    (INFRASTRUCTURE_GROUP, "GCPMachine"): _gcp_machine,
    (INFRASTRUCTURE_GROUP, "GCPManagedControlPlane"): _gcp_managed_control_plane,
    (INFRASTRUCTURE_GROUP, "GCPManagedMachinePool"): _gcp_managed_machine_pool,
    (GATEWAY_GROUP, "GRPCRoute"): _grpc_route,
    (GATEWAY_GROUP, "HTTPRoute"): _http_route,
    (BATCH_GROUP, "Job"): _job,
    (KARPENTER_AWS_GROUP, "EC2NodeClass"): _karpenter_ec2_node_class,
    (KARPENTER_GROUP, "NodeClaim"): _karpenter_node_claim,
    (KARPENTER_GROUP, "NodePool"): _karpenter_node_pool,
    (KEDA_GROUP, "ScaledObject"): _keda_scaled_object,
    (KEDA_GROUP, "ScaledJob"): _keda_scaled_job,
    (COMPLIANCE_GROUP, "SbomReport"): _sbom_report,
    (COMPLIANCE_GROUP, "ClusterSbomReport"): _sbom_report,
    (COMPLIANCE_GROUP, "VulnerabilityReport"): _vulnerability_report,
    (PROMETHEUS_GROUP, "PrometheusRule"): _prometheus_rule,
    (GATEWAY_GROUP, "TCPRoute"): _tcp_route,
    (GATEWAY_GROUP, "TLSRoute"): _tls_route,
}

PROVIDER_DETAIL_MATCHERS: tuple[ProviderDetailMatcher, ...] = (
    _crossplane_managed_resource,
    _crossplane_composite,
)


def _conditions(raw: Mapping[str, Any]) -> list[ProviderCondition]:
    items = _mapping_items_at(raw, "status", "v1beta2", "conditions")
    if not items:
        items = _mapping_items_at(raw, "status", "conditions")
    return _condition_items(items)


def _condition_items(value: object) -> list[ProviderCondition]:
    result: list[ProviderCondition] = []
    for item in _mapping_items(value):
        condition_type = _text(item.get("type"))
        status = _text(item.get("status"))
        if condition_type is None or status not in {"True", "False", "Unknown"}:
            continue
        result.append(
            ProviderCondition(
                type=condition_type,
                status=status,
                reason=_text(item.get("reason")),
                message=_text(item.get("message")),
                last_transition_time=_text(item.get("lastTransitionTime")),
            )
        )
    return result


def _condition_truth_from(conditions: list[ProviderCondition], condition_type: str) -> bool | None:
    condition = _condition_by_type(conditions, condition_type)
    if condition is None:
        return None
    return True if condition.status == "True" else False if condition.status == "False" else None


def _condition_by_type(
    conditions: list[ProviderCondition], condition_type: str
) -> ProviderCondition | None:
    return next(
        (condition for condition in conditions if condition.type == condition_type),
        None,
    )


def _endpoint_access(
    raw: Mapping[str, Any],
) -> str | None:
    public = _bool_at(raw, "spec", "endpointAccess", "public")
    private = _bool_at(raw, "spec", "endpointAccess", "private")
    if public is True and private is True:
        return "public-and-private"
    if public is True:
        return "public"
    if private is True:
        return "private"
    return None


def _first_endpoint(*items: Mapping[str, Any]) -> str | None:
    for item in items:
        host = _text(item.get("host"))
        if host is None:
            continue
        port = _int(item.get("port"))
        return f"{host}:{port}" if port not in (None, 443) else host
    return None


def _api_group(value: object) -> str:
    text = _text(value) or ""
    return text.split("/", 1)[0] if "/" in text else ""


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_at(value: Mapping[str, Any], *path: str) -> dict[str, Any]:
    current: object = value
    for key in path:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key)
    return _mapping(current)


def _value_at(value: Mapping[str, Any], *path: str) -> object:
    current: object = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:MAX_TEXT_LENGTH] if normalized else None


def _bounded_text(value: object, limit: int) -> str | None:
    normalized = _text(value)
    if normalized is None or len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 1)]}…"


def _text_at(value: Mapping[str, Any], *path: str) -> str | None:
    return _text(_value_at(value, *path))


def _bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _bool_text(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def _bool_at(value: Mapping[str, Any], *path: str) -> bool | None:
    return _bool(_value_at(value, *path))


def _int_at(value: Mapping[str, Any], *path: str) -> int | None:
    raw = _value_at(value, *path)
    return _int(raw)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _nonnegative_safe_int_at(value: Mapping[str, Any], *path: str) -> int | None:
    parsed = _int_at(value, *path)
    return parsed if parsed is not None and 0 <= parsed <= MAX_SAFE_INTEGER else None


def _first_present_int(*values: int | None) -> int | None:
    return next((value for value in values if value is not None), None)


def _scalar_text_at(value: Mapping[str, Any], *path: str) -> str | None:
    return _scalar_text(_value_at(value, *path))


def _scalar_text(value: object) -> str | None:
    return (
        _text(value) if isinstance(value, str) else str(value) if _int(value) is not None else None
    )


def _standard_replicas(raw: Mapping[str, Any]) -> ProviderReplicas:
    return ProviderReplicas(
        desired=_int_at(raw, "spec", "replicas"),
        ready=_int_at(raw, "status", "readyReplicas"),
        available=_int_at(raw, "status", "availableReplicas"),
        up_to_date=_int_at(raw, "status", "upToDateReplicas")
        if _int_at(raw, "status", "upToDateReplicas") is not None
        else _int_at(raw, "status", "updatedReplicas"),
    )


def _reference_at(value: Mapping[str, Any], *path: str) -> ProviderReference | None:
    item = _mapping_at(value, *path)
    kind = _text(item.get("kind"))
    name = _text(item.get("name"))
    if kind is None or name is None:
        return None
    return ProviderReference(
        api_version=_text(item.get("apiVersion")),
        kind=kind,
        namespace=_text(item.get("namespace")),
        name=name,
    )


def _named_reference_at(value: Mapping[str, Any], *path: str) -> ProviderNamedReference | None:
    return _named_reference(_mapping_at(value, *path))


def _named_reference(value: Mapping[str, Any]) -> ProviderNamedReference | None:
    name = _text(value.get("name"))
    if name is None:
        return None
    return ProviderNamedReference(
        api_version=_text(value.get("apiVersion")) or _text(value.get("group")),
        kind=_text(value.get("kind")),
        namespace=_text(value.get("namespace")),
        name=name,
    )


def _named_reference_with_namespace(
    value: Mapping[str, Any], default_namespace: str | None
) -> ProviderNamedReference | None:
    reference = _named_reference(value)
    if reference is None or reference.namespace is not None or default_namespace is None:
        return reference
    return reference.model_copy(update={"namespace": default_namespace})


def _cluster_name(raw: Mapping[str, Any]) -> str | None:
    return _text_at(raw, "spec", "clusterName") or _text_at(
        raw, "metadata", "labels", "cluster.x-k8s.io/cluster-name"
    )


def _condition_truth(raw: Mapping[str, Any], condition_type: str) -> bool | None:
    return _condition_truth_from(_conditions(raw), condition_type)


def _provider_from_kind(kind: str) -> str | None:
    lowered = kind.lower()
    for prefix, label in (
        ("aws", "AWS"),
        ("azure", "Azure"),
        ("gcp", "GCP"),
        ("vsphere", "vSphere"),
        ("docker", "Docker"),
    ):
        if lowered.startswith(prefix):
            return label
    return None


def _provider_id_parts(provider_id: str | None) -> tuple[str | None, str | None, str | None]:
    if provider_id is None:
        return None, None, None
    if provider_id.startswith("aws://"):
        parts = provider_id.removeprefix("aws://").lstrip("/").split("/")
        return "AWS", parts[0] if parts else None, parts[1] if len(parts) > 1 else None
    if provider_id.startswith("gce://"):
        parts = provider_id.removeprefix("gce://").lstrip("/").split("/")
        return "GCP", parts[1] if len(parts) > 1 else None, parts[2] if len(parts) > 2 else None
    if provider_id.startswith("azure://"):
        parts = provider_id.removeprefix("azure://").lstrip("/").split("/")
        resource_group = _path_value(parts, "resourceGroups")
        instance = _path_value(parts, "virtualMachines")
        return "Azure", resource_group, instance
    if provider_id.startswith("vsphere://"):
        return "vSphere", None, provider_id.removeprefix("vsphere://").lstrip("/") or None
    return None, None, None


def _path_value(parts: list[str], marker: str) -> str | None:
    try:
        index = parts.index(marker)
    except ValueError:
        return None
    return parts[index + 1] if index + 1 < len(parts) else None


def _mapping_items_at(value: Mapping[str, Any], *path: str) -> list[dict[str, Any]]:
    return _mapping_items(_value_at(value, *path))


def _mapping_items(raw: object) -> list[dict[str, Any]]:
    return _bounded_mapping_items(raw, MAX_COLLECTION_ITEMS)


def _bounded_mapping_items(raw: object, limit: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [_mapping(item) for item in raw[:limit] if isinstance(item, Mapping)]


def _text_items_at(value: Mapping[str, Any], *path: str) -> list[str]:
    return _text_items(_value_at(value, *path))


def _text_items(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [text for item in raw[:MAX_COLLECTION_ITEMS] if (text := _text(item)) is not None]


def _mapping_keys_at(value: Mapping[str, Any], *path: str) -> list[str]:
    return sorted(_mapping_at(value, *path))[:MAX_COLLECTION_ITEMS]


def _key_values_at(value: Mapping[str, Any], *path: str) -> list[ProviderKeyValue]:
    result: list[ProviderKeyValue] = []
    for raw_key, raw in sorted(_mapping_at(value, *path).items())[:MAX_COLLECTION_ITEMS]:
        key = _text(raw_key)
        normalized = _text(str(raw)) if isinstance(raw, (str, int, float, bool)) else None
        if key is not None and normalized is not None:
            result.append(ProviderKeyValue(key=key, value=normalized))
    return result


def _safe_key_values_at(value: Mapping[str, Any], *path: str) -> list[ProviderKeyValue]:
    return _safe_key_values(_mapping_at(value, *path))


def _safe_key_values(value: Mapping[str, Any]) -> list[ProviderKeyValue]:
    result: list[ProviderKeyValue] = []
    for raw_key, raw in sorted(value.items())[:MAX_COLLECTION_ITEMS]:
        key = _text(raw_key)
        normalized = _text(str(raw)) if isinstance(raw, (str, int, float, bool)) else None
        if key is not None and not _sensitive_dynamic_key(key) and normalized is not None:
            result.append(ProviderKeyValue(key=key, value=normalized))
    return result


def _sensitive_dynamic_key(value: str) -> bool:
    normalized = value.strip().lower().replace("_", "-")
    return any(part in normalized for part in SENSITIVE_DYNAMIC_KEY_PARTS)


def _present_at(value: Mapping[str, Any], *path: str) -> bool | None:
    raw = _value_at(value, *path)
    return None if raw is None else bool(raw)


def _collection_length_at(value: Mapping[str, Any], *path: str) -> int | None:
    raw = _value_at(value, *path)
    return len(raw) if isinstance(raw, list) else None


def _joined_text(*values: object) -> str | None:
    parts = [
        part
        for value in values
        if (part := _text(value) or (str(value) if _int(value) is not None else None)) is not None
    ]
    return "/".join(parts) if parts else None
