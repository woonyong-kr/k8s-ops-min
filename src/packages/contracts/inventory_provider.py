"""Typed, redacted provider detail projections for dynamic inventory resources."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from packages.contracts.modeling import StrictModel


class ProviderCondition(StrictModel):
    type: str
    status: Literal["True", "False", "Unknown"]
    reason: str | None = None
    message: str | None = None
    last_transition_time: str | None = None


class ProviderAddress(StrictModel):
    type: str
    address: str


class ProviderKeyValue(StrictModel):
    key: str
    value: str


class ProviderScaling(StrictModel):
    minimum: int | None = None
    maximum: int | None = None
    current: int | None = None


class ProviderReference(StrictModel):
    api_version: str | None = None
    kind: str
    namespace: str | None = None
    name: str


class ProviderNamedReference(StrictModel):
    """A redacted object reference whose source may omit Kubernetes type metadata."""

    api_version: str | None = None
    kind: str | None = None
    namespace: str | None = None
    name: str


class ProviderReplicas(StrictModel):
    desired: int | None = None
    ready: int | None = None
    available: int | None = None
    up_to_date: int | None = None


class CapiUnhealthyCondition(StrictModel):
    type: str
    status: str | None = None
    timeout: str | None = None


class AwsMachineProviderDetail(StrictModel):
    type: Literal["aws-machine"] = "aws-machine"
    instance_type: str | None = None
    instance_id: str | None = None
    instance_state: str | None = None
    provider_id: str | None = None
    iam_instance_profile: str | None = None
    ssh_key_name: str | None = None
    subnet_id: str | None = None
    secrets_backend: str | None = None
    addresses: list[ProviderAddress] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class AwsManagedClusterProviderDetail(StrictModel):
    type: Literal["aws-managed-cluster"] = "aws-managed-cluster"
    endpoint: str | None = None
    failure_domains: list[str] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class AwsSubnet(StrictModel):
    id: str | None = None
    availability_zone: str | None = None
    public: bool | None = None
    cidr_block: str | None = None


class AwsSecurityGroup(StrictModel):
    role: str
    id: str | None = None
    name: str | None = None


class AwsAddon(StrictModel):
    name: str
    requested_version: str | None = None
    current_version: str | None = None
    status: str | None = None


class AwsManagedControlPlaneProviderDetail(StrictModel):
    type: Literal["aws-managed-control-plane"] = "aws-managed-control-plane"
    cluster_name: str | None = None
    region: str | None = None
    version: str | None = None
    endpoint_access: Literal["public", "private", "public-and-private"] | None = None
    role_name: str | None = None
    identity: str | None = None
    vpc_id: str | None = None
    vpc_cidr_block: str | None = None
    subnets: list[AwsSubnet] = Field(default_factory=list)
    security_groups: list[AwsSecurityGroup] = Field(default_factory=list)
    nat_gateway_ips: list[str] = Field(default_factory=list)
    failure_domains: list[str] = Field(default_factory=list)
    addons: list[AwsAddon] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class AwsManagedMachinePoolProviderDetail(StrictModel):
    type: Literal["aws-managed-machine-pool"] = "aws-managed-machine-pool"
    node_group_name: str | None = None
    instance_type: str | None = None
    ami_type: str | None = None
    capacity_type: str | None = None
    role_name: str | None = None
    scaling: ProviderScaling
    max_unavailable: int | None = None
    subnet_ids: list[str] = Field(default_factory=list)
    labels: list[ProviderKeyValue] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class AzureMachineProviderDetail(StrictModel):
    type: Literal["azure-machine"] = "azure-machine"
    vm_size: str | None = None
    availability_zone: str | None = None
    os_type: str | None = None
    os_disk_size_gb: int | None = None
    provider_id: str | None = None
    subnet_name: str | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class AzureManagedControlPlaneProviderDetail(StrictModel):
    type: Literal["azure-managed-control-plane"] = "azure-managed-control-plane"
    location: str | None = None
    resource_group_name: str | None = None
    version: str | None = None
    sku_tier: str | None = None
    dns_prefix: str | None = None
    subscription_id: str | None = None
    network_plugin: str | None = None
    network_policy: str | None = None
    private_cluster: bool | None = None
    dns_service_ip: str | None = None
    load_balancer_sku: str | None = None
    upgrade_channel: str | None = None
    authorized_ip_ranges: list[str] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class ProviderTaint(StrictModel):
    key: str
    value: str | None = None
    effect: str | None = None


class AzureManagedMachinePoolProviderDetail(StrictModel):
    type: Literal["azure-managed-machine-pool"] = "azure-managed-machine-pool"
    pool_name: str | None = None
    vm_size: str | None = None
    mode: str | None = None
    os_type: str | None = None
    os_disk_type: str | None = None
    os_disk_size_gb: int | None = None
    priority: str | None = None
    max_pods: int | None = None
    scaling: ProviderScaling
    scale_down_mode: str | None = None
    availability_zones: list[str] = Field(default_factory=list)
    labels: list[ProviderKeyValue] = Field(default_factory=list)
    taints: list[ProviderTaint] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class CapiClusterProviderDetail(StrictModel):
    type: Literal["capi-cluster"] = "capi-cluster"
    phase: str | None = None
    version: str | None = None
    cluster_class: str | None = None
    endpoint: str | None = None
    provider: str | None = None
    paused: bool = False
    control_plane: ProviderReplicas
    workers: ProviderReplicas
    control_plane_ref: ProviderReference | None = None
    infrastructure_ref: ProviderReference | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class CapiKubeadmControlPlaneProviderDetail(StrictModel):
    type: Literal["capi-kubeadm-control-plane"] = "capi-kubeadm-control-plane"
    cluster_name: str | None = None
    version: str | None = None
    initialized: bool | None = None
    update_strategy: str | None = None
    replicas: ProviderReplicas
    infrastructure_ref: ProviderReference | None = None
    node_drain_timeout: str | None = None
    node_volume_detach_timeout: str | None = None
    node_deletion_timeout: str | None = None
    certificate_sans: list[str] = Field(default_factory=list)
    remediation_machine: str | None = None
    remediation_retry_count: int | None = None
    remediation_timestamp: str | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class CapiMachineDeploymentProviderDetail(StrictModel):
    type: Literal["capi-machine-deployment"] = "capi-machine-deployment"
    phase: str | None = None
    cluster_name: str | None = None
    version: str | None = None
    paused: bool = False
    replicas: ProviderReplicas
    strategy_type: str | None = None
    max_surge: str | None = None
    max_unavailable: str | None = None
    infrastructure_ref: ProviderReference | None = None
    bootstrap_ref: ProviderReference | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class CapiMachineHealthCheckProviderDetail(StrictModel):
    type: Literal["capi-machine-health-check"] = "capi-machine-health-check"
    cluster_name: str | None = None
    expected_machines: int | None = None
    current_healthy: int | None = None
    remediations_allowed: int | None = None
    node_startup_timeout: str | None = None
    max_unhealthy: str | None = None
    unhealthy_range: str | None = None
    selector: list[ProviderKeyValue] = Field(default_factory=list)
    unhealthy_conditions: list[CapiUnhealthyCondition] = Field(default_factory=list)
    remediation_template: ProviderReference | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class CapiMachinePoolProviderDetail(StrictModel):
    type: Literal["capi-machine-pool"] = "capi-machine-pool"
    phase: str | None = None
    cluster_name: str | None = None
    min_ready_seconds: int | None = None
    replicas: ProviderReplicas
    infrastructure_ref: ProviderReference | None = None
    bootstrap_ref: ProviderReference | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class CapiMachineProviderDetail(StrictModel):
    type: Literal["capi-machine"] = "capi-machine"
    phase: str | None = None
    role: Literal["control-plane", "worker"]
    cluster_name: str | None = None
    version: str | None = None
    failure_domain: str | None = None
    provider: str | None = None
    provider_id: str | None = None
    provider_region: str | None = None
    provider_instance_id: str | None = None
    node_name: str | None = None
    node_uid: str | None = None
    bootstrap_ref: ProviderReference | None = None
    infrastructure_ref: ProviderReference | None = None
    addresses: list[ProviderAddress] = Field(default_factory=list)
    os_image: str | None = None
    architecture: str | None = None
    kernel_version: str | None = None
    container_runtime_version: str | None = None
    kubelet_version: str | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class CapiMachineSetProviderDetail(StrictModel):
    type: Literal["capi-machine-set"] = "capi-machine-set"
    cluster_name: str | None = None
    delete_policy: str | None = None
    min_ready_seconds: int | None = None
    replicas: ProviderReplicas
    infrastructure_ref: ProviderReference | None = None
    bootstrap_ref: ProviderReference | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class CertificatePrivateKeyDetail(StrictModel):
    algorithm: str | None = None
    size: int | None = None
    encoding: str | None = None
    rotation_policy: str | None = None


class CertificateProviderDetail(StrictModel):
    type: Literal["certificate"] = "certificate"
    ready: bool | None = None
    secret_name: str | None = None
    revision: int | None = None
    is_ca: bool | None = None
    duration: str | None = None
    renew_before: str | None = None
    not_before: str | None = None
    not_after: str | None = None
    renewal_time: str | None = None
    failed_issuance_attempts: int | None = None
    last_failure_time: str | None = None
    private_key: CertificatePrivateKeyDetail | None = None
    dns_names: list[str] = Field(default_factory=list)
    issuer_ref: ProviderNamedReference | None = None
    usages: list[str] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class CertificateRequestProviderDetail(StrictModel):
    type: Literal["certificate-request"] = "certificate-request"
    ready: bool | None = None
    approved: bool | None = None
    denied: bool | None = None
    issuer_ref: ProviderNamedReference | None = None
    owner_certificate: ProviderNamedReference | None = None
    duration: str | None = None
    usages: list[str] = Field(default_factory=list)
    certificate_issued: bool | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class ComplianceControlDetail(StrictModel):
    id: str
    name: str | None = None
    description: str | None = None
    severity: str | None = None
    total_pass: int | None = None
    total_fail: int | None = None
    check_ids: list[str] = Field(default_factory=list)


class ClusterComplianceReportProviderDetail(StrictModel):
    type: Literal["cluster-compliance-report"] = "cluster-compliance-report"
    framework_id: str | None = None
    framework_title: str | None = None
    framework_description: str | None = None
    framework_version: str | None = None
    platform: str | None = None
    updated_at: str | None = None
    pass_count: int | None = None
    fail_count: int | None = None
    controls: list[ComplianceControlDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class CrossplaneCompositeProviderDetail(StrictModel):
    type: Literal["crossplane-composite"] = "crossplane-composite"
    claim: bool
    paused: bool
    composition_ref: ProviderNamedReference | None = None
    composition_revision_ref: ProviderNamedReference | None = None
    composition_update_policy: str | None = None
    bound_resource_ref: ProviderNamedReference | None = None
    composed_resource_refs: list[ProviderNamedReference] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class CrossplaneManagedResourceProviderDetail(StrictModel):
    type: Literal["crossplane-managed-resource"] = "crossplane-managed-resource"
    api_group: str | None = None
    kind: str
    external_name: str | None = None
    management_policies: list[str] = Field(default_factory=list)
    deletion_policy: str | None = None
    paused: bool
    provider_config_ref: ProviderNamedReference | None = None
    composing_resource_ref: ProviderNamedReference | None = None
    observed_spec_fields: list[str] = Field(default_factory=list)
    observed_status_fields: list[str] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class CronWorkflowProviderDetail(StrictModel):
    type: Literal["cron-workflow"] = "cron-workflow"
    schedules: list[str] = Field(default_factory=list)
    timezone: str | None = None
    suspended: bool | None = None
    concurrency_policy: str | None = None
    last_scheduled_time: str | None = None
    active_workflows: list[ProviderNamedReference] = Field(default_factory=list)
    workflow_template_ref: ProviderNamedReference | None = None
    workflow_template_cluster_scope: bool | None = None
    entrypoint: str | None = None
    argument_count: int | None = None
    template_count: int | None = None
    successful_history_limit: int | None = None
    failed_history_limit: int | None = None
    starting_deadline_seconds: int | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class ExternalSecretMappingDetail(StrictModel):
    secret_key: str | None = None
    remote_key: str | None = None
    remote_property: str | None = None
    remote_version: str | None = None


class ExternalSecretSourceDetail(StrictModel):
    type: Literal["extract", "find", "source-ref", "unknown"]
    detail: str | None = None


class ExternalSecretProviderDetail(StrictModel):
    type: Literal["external-secret"] = "external-secret"
    ready: bool | None = None
    last_sync_time: str | None = None
    refresh_interval: str | None = None
    target_name: str | None = None
    synced_resource_version: str | None = None
    binding_name: str | None = None
    store_name: str | None = None
    store_kind: str | None = None
    mappings: list[ExternalSecretMappingDetail] = Field(default_factory=list)
    data_sources: list[ExternalSecretSourceDetail] = Field(default_factory=list)
    target_creation_policy: str | None = None
    target_deletion_policy: str | None = None
    template_type: str | None = None
    template_engine_version: str | None = None
    template_labels: list[ProviderKeyValue] = Field(default_factory=list)
    template_annotations: list[ProviderKeyValue] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class PersistentVolumeClaimProviderDetail(StrictModel):
    type: Literal["persistent-volume-claim"] = "persistent-volume-claim"
    phase: str | None = None
    capacity: str | None = None
    requested: str | None = None
    storage_class_name: str | None = None
    access_modes: list[str] = Field(default_factory=list)
    volume_mode: str | None = None
    volume_name: str | None = None
    provisioner: str | None = None
    selected_node: str | None = None
    bind_completed: bool | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class SealedSecretProviderDetail(StrictModel):
    type: Literal["sealed-secret"] = "sealed-secret"
    synced: bool | None = None
    target_secret_name: str | None = None
    secret_type: str | None = None
    scope: Literal["strict", "namespace-wide", "cluster-wide"]
    observed_generation: int | None = None
    encrypted_keys: list[str] = Field(default_factory=list)
    template_labels: list[ProviderKeyValue] = Field(default_factory=list)
    template_annotations: list[ProviderKeyValue] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class SecretProviderDetail(StrictModel):
    type: Literal["secret"] = "secret"
    secret_type: str | None = None
    immutable: bool | None = None
    key_names: list[str] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class SecretStoreProviderDetail(StrictModel):
    type: Literal["secret-store"] = "secret-store"
    cluster_scope: bool
    ready: bool | None = None
    provider_key: str | None = None
    provider_type: str | None = None
    provider_details: list[ProviderKeyValue] = Field(default_factory=list)
    controller: str | None = None
    max_retries: int | None = None
    retry_interval: str | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class WorkflowExecutionNodeDetail(StrictModel):
    id: str
    label: str
    node_type: str
    phase: str
    depth: int = Field(ge=0, le=20)
    started_at: str | None = None
    finished_at: str | None = None
    message: str | None = None
    template_ref: ProviderNamedReference | None = None


class WorkflowProviderDetail(StrictModel):
    type: Literal["workflow"] = "workflow"
    phase: str
    started_at: str | None = None
    finished_at: str | None = None
    progress: str | None = None
    estimated_duration_seconds: int | None = None
    workflow_template_ref: ProviderNamedReference | None = None
    argument_names: list[str] = Field(default_factory=list)
    resource_durations: list[ProviderKeyValue] = Field(default_factory=list)
    execution_nodes: list[WorkflowExecutionNodeDetail] = Field(default_factory=list)
    observed_node_count: int = Field(ge=0)
    projected_node_count: int = Field(ge=0)
    truncated: bool
    problem_summaries: list[str] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class GatewayClassProviderDetail(StrictModel):
    type: Literal["gateway-class"] = "gateway-class"
    controller_name: str | None = None
    description: str | None = None
    accepted: bool | None = None
    parameters_ref: ProviderNamedReference | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class GcpAdditionalDiskDetail(StrictModel):
    device_type: str | None = None
    size_gb: int | None = None


class GcpMachineProviderDetail(StrictModel):
    type: Literal["gcp-machine"] = "gcp-machine"
    ready: bool | None = None
    instance_type: str | None = None
    zone: str | None = None
    instance_id: str | None = None
    image: str | None = None
    additional_disks: list[GcpAdditionalDiskDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class GcpAuthorizedNetworkDetail(StrictModel):
    name: str | None = None
    cidr: str


class GcpManagedControlPlaneProviderDetail(StrictModel):
    type: Literal["gcp-managed-control-plane"] = "gcp-managed-control-plane"
    ready: bool | None = None
    cluster_name: str | None = None
    project: str | None = None
    location: str | None = None
    version: str | None = None
    release_channel: str | None = None
    autopilot: bool | None = None
    endpoint: str | None = None
    pod_cidr: str | None = None
    service_cidr: str | None = None
    ip_aliases: bool | None = None
    logging_service: str | None = None
    monitoring_service: str | None = None
    authorized_networks: list[GcpAuthorizedNetworkDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class GcpManagedMachinePoolProviderDetail(StrictModel):
    type: Literal["gcp-managed-machine-pool"] = "gcp-managed-machine-pool"
    ready: bool | None = None
    node_pool_name: str | None = None
    machine_type: str | None = None
    disk_type: str | None = None
    disk_size_gb: int | None = None
    image_type: str | None = None
    max_pods_per_node: int | None = None
    autoscaling_enabled: bool | None = None
    scaling: ProviderScaling
    auto_repair: bool | None = None
    auto_upgrade: bool | None = None
    node_locations: list[str] = Field(default_factory=list)
    labels: list[ProviderKeyValue] = Field(default_factory=list)
    taints: list[ProviderTaint] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class GatewayRouteMatchDetail(StrictModel):
    method: str | None = None
    path_type: str | None = None
    path_value: str | None = None
    grpc_type: str | None = None
    grpc_service: str | None = None
    grpc_method: str | None = None
    headers: list[ProviderKeyValue] = Field(default_factory=list)
    query_params: list[ProviderKeyValue] = Field(default_factory=list)


class GatewayRouteBackendDetail(StrictModel):
    reference: ProviderNamedReference
    port: int | None = None
    weight: int | None = None


class GatewayRouteFilterDetail(StrictModel):
    type: str
    summary: str | None = None


class GatewayRouteRuleDetail(StrictModel):
    matches: list[GatewayRouteMatchDetail] = Field(default_factory=list)
    backends: list[GatewayRouteBackendDetail] = Field(default_factory=list)
    filters: list[GatewayRouteFilterDetail] = Field(default_factory=list)


class GatewayRouteParentStatusDetail(StrictModel):
    reference: ProviderNamedReference | None = None
    section_name: str | None = None
    accepted: bool | None = None
    resolved_refs: bool | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class GrpcRouteProviderDetail(StrictModel):
    type: Literal["grpc-route"] = "grpc-route"
    hostnames: list[str] = Field(default_factory=list)
    parent_refs: list[ProviderNamedReference] = Field(default_factory=list)
    rules: list[GatewayRouteRuleDetail] = Field(default_factory=list)
    parent_statuses: list[GatewayRouteParentStatusDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class HttpRouteProviderDetail(StrictModel):
    type: Literal["http-route"] = "http-route"
    hostnames: list[str] = Field(default_factory=list)
    parent_refs: list[ProviderNamedReference] = Field(default_factory=list)
    rules: list[GatewayRouteRuleDetail] = Field(default_factory=list)
    parent_statuses: list[GatewayRouteParentStatusDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class JobProviderDetail(StrictModel):
    type: Literal["job"] = "job"
    state: Literal["completed", "failed", "suspended", "running", "pending"]
    succeeded: int | None = None
    failed: int | None = None
    active: int | None = None
    completions: int | None = None
    parallelism: int | None = None
    backoff_limit: int | None = None
    active_deadline_seconds: int | None = None
    ttl_seconds_after_finished: int | None = None
    suspended: bool | None = None
    start_time: str | None = None
    completion_time: str | None = None
    terminal_reason: str | None = None
    terminal_message: str | None = None
    conditions: list[ProviderCondition] = Field(default_factory=list)


class ProviderRequirementDetail(StrictModel):
    key: str
    operator: str | None = None
    values: list[str] = Field(default_factory=list)
    min_values: int | None = None


class KarpenterSelectorTermDetail(StrictModel):
    id: str | None = None
    name: str | None = None
    alias: str | None = None
    owner: str | None = None
    tags: list[ProviderKeyValue] = Field(default_factory=list)


class KarpenterBlockDeviceDetail(StrictModel):
    device_name: str | None = None
    volume_type: str | None = None
    volume_size: str | None = None
    iops: int | None = None
    throughput: int | None = None
    encrypted: bool | None = None
    delete_on_termination: bool | None = None


class KarpenterResolvedAmiDetail(StrictModel):
    id: str
    name: str | None = None
    requirements: list[ProviderRequirementDetail] = Field(default_factory=list)


class KarpenterResolvedNetworkDetail(StrictModel):
    id: str
    name: str | None = None
    zone: str | None = None


class KarpenterMetadataOptionsDetail(StrictModel):
    http_tokens: str | None = None
    http_put_response_hop_limit: int | None = None
    http_endpoint: str | None = None


class KarpenterEc2NodeClassProviderDetail(StrictModel):
    type: Literal["karpenter-ec2-node-class"] = "karpenter-ec2-node-class"
    ready: bool | None = None
    role: str | None = None
    instance_profile: str | None = None
    ami_family: str | None = None
    ami_selector_terms: list[KarpenterSelectorTermDetail] = Field(default_factory=list)
    block_devices: list[KarpenterBlockDeviceDetail] = Field(default_factory=list)
    subnet_selector_terms: list[KarpenterSelectorTermDetail] = Field(default_factory=list)
    security_group_selector_terms: list[KarpenterSelectorTermDetail] = Field(default_factory=list)
    metadata_options: KarpenterMetadataOptionsDetail | None = None
    resolved_amis: list[KarpenterResolvedAmiDetail] = Field(default_factory=list)
    resolved_subnets: list[KarpenterResolvedNetworkDetail] = Field(default_factory=list)
    resolved_security_groups: list[KarpenterResolvedNetworkDetail] = Field(default_factory=list)
    tags: list[ProviderKeyValue] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class KarpenterCapacityDetail(StrictModel):
    cpu: str | None = None
    memory: str | None = None
    pods: str | None = None
    ephemeral_storage: str | None = None


class KarpenterNodeClaimProviderDetail(StrictModel):
    type: Literal["karpenter-node-claim"] = "karpenter-node-claim"
    state: Literal[
        "ready",
        "registered",
        "launched",
        "initialized",
        "not-ready",
        "pending",
        "unknown",
    ]
    instance_type: str | None = None
    capacity_type: str | None = None
    node_name: str | None = None
    zone: str | None = None
    architecture: str | None = None
    node_pool: str | None = None
    node_class_ref: ProviderNamedReference | None = None
    image_id: str | None = None
    expire_after: str | None = None
    capacity: KarpenterCapacityDetail
    requirements: list[ProviderRequirementDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class KarpenterDisruptionBudgetDetail(StrictModel):
    nodes: str | None = None
    schedule: str | None = None
    duration: str | None = None


class KarpenterNodePoolProviderDetail(StrictModel):
    type: Literal["karpenter-node-pool"] = "karpenter-node-pool"
    ready: bool | None = None
    node_class_ref: ProviderNamedReference | None = None
    limit_cpu: str | None = None
    limit_memory: str | None = None
    weight: int | None = None
    current_cpu: str | None = None
    current_memory: str | None = None
    consolidation_policy: str | None = None
    consolidate_after: str | None = None
    expire_after: str | None = None
    disruption_budgets: list[KarpenterDisruptionBudgetDetail] = Field(default_factory=list)
    template_labels: list[ProviderKeyValue] = Field(default_factory=list)
    template_taints: list[ProviderTaint] = Field(default_factory=list)
    startup_taints: list[ProviderTaint] = Field(default_factory=list)
    requirements: list[ProviderRequirementDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class KedaTriggerDetail(StrictModel):
    type: str
    name: str | None = None
    authentication_ref: ProviderNamedReference | None = None
    metadata_keys: list[str] = Field(default_factory=list)
    redacted_metadata_count: int = 0


class KedaScalingPolicyDetail(StrictModel):
    direction: Literal["up", "down"]
    type: str | None = None
    value: int | None = None
    period_seconds: int | None = None


class KedaScaledObjectProviderDetail(StrictModel):
    type: Literal["keda-scaled-object"] = "keda-scaled-object"
    state: Literal["paused", "fallback", "not-ready", "active", "idle", "ready", "unknown"]
    target_ref: ProviderNamedReference | None = None
    scaling: ProviderScaling
    idle_replicas: int | None = None
    polling_interval_seconds: int | None = None
    cooldown_period_seconds: int | None = None
    hpa_name: str | None = None
    last_active_time: str | None = None
    fallback_failure_threshold: int | None = None
    fallback_replicas: int | None = None
    restore_original_replicas: bool | None = None
    scale_up_stabilization_seconds: int | None = None
    scale_down_stabilization_seconds: int | None = None
    scaling_policies: list[KedaScalingPolicyDetail] = Field(default_factory=list)
    triggers: list[KedaTriggerDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class KedaScaledJobProviderDetail(StrictModel):
    type: Literal["keda-scaled-job"] = "keda-scaled-job"
    state: Literal["not-ready", "active", "idle", "ready", "unknown"]
    job_target_name: str | None = None
    strategy: str | None = None
    polling_interval_seconds: int | None = None
    successful_history_limit: int | None = None
    failed_history_limit: int | None = None
    minimum_replicas: int | None = None
    maximum_replicas: int | None = None
    triggers: list[KedaTriggerDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class SecuritySeveritySummaryDetail(StrictModel):
    critical: int = Field(ge=0)
    high: int = Field(ge=0)
    medium: int = Field(ge=0)
    low: int = Field(ge=0)
    unknown: int = Field(ge=0)


class SbomComponentDetail(StrictModel):
    name: str
    version: str | None = None
    type: str | None = None
    package_url: str | None = None
    package_url_qualifiers_redacted: bool = False
    license: str | None = None


class SbomReportProviderDetail(StrictModel):
    type: Literal["sbom-report"] = "sbom-report"
    container_name: str | None = None
    image: str | None = None
    bom_format: str | None = None
    spec_version: str | None = None
    component_count: int = Field(ge=0)
    dependency_count: int = Field(ge=0)
    observed_component_count: int = Field(ge=0)
    projected_component_count: int = Field(ge=0)
    truncated: bool
    scanner_name: str | None = None
    scanner_version: str | None = None
    scanned_at: str | None = None
    components: list[SbomComponentDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class VulnerabilityFindingDetail(StrictModel):
    vulnerability_id: str
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
    score: float | None = Field(default=None, ge=0, le=10)
    package: str | None = None
    installed_version: str | None = None
    fixed_version: str | None = None
    primary_link: str | None = None


class VulnerabilityReportProviderDetail(StrictModel):
    type: Literal["vulnerability-report"] = "vulnerability-report"
    container_name: str | None = None
    image: str | None = None
    os_family: str | None = None
    os_name: str | None = None
    os_end_of_service_life: bool | None = None
    scanner_name: str | None = None
    scanner_version: str | None = None
    scanned_at: str | None = None
    severity: SecuritySeveritySummaryDetail
    observed_vulnerability_count: int = Field(ge=0)
    projected_vulnerability_count: int = Field(ge=0)
    truncated: bool
    vulnerabilities: list[VulnerabilityFindingDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class PrometheusRuleEntryDetail(StrictModel):
    type: Literal["alert", "recording"]
    name: str
    expression: str
    duration: str | None = None
    severity: str | None = None
    summary: str | None = None
    description: str | None = None
    labels: list[ProviderKeyValue] = Field(default_factory=list)


class PrometheusRuleGroupDetail(StrictModel):
    name: str
    interval: str | None = None
    rule_count: int
    alert_count: int
    recording_count: int
    rules: list[PrometheusRuleEntryDetail] = Field(default_factory=list)


class PrometheusRuleProviderDetail(StrictModel):
    type: Literal["prometheus-rule"] = "prometheus-rule"
    group_count: int
    total_rules: int
    total_alerts: int
    total_recordings: int
    projected_rules: int
    truncated: bool
    groups: list[PrometheusRuleGroupDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class TcpRouteProviderDetail(StrictModel):
    type: Literal["tcp-route"] = "tcp-route"
    hostnames: list[str] = Field(default_factory=list)
    parent_refs: list[ProviderNamedReference] = Field(default_factory=list)
    rules: list[GatewayRouteRuleDetail] = Field(default_factory=list)
    parent_statuses: list[GatewayRouteParentStatusDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


class TlsRouteProviderDetail(StrictModel):
    type: Literal["tls-route"] = "tls-route"
    hostnames: list[str] = Field(default_factory=list)
    parent_refs: list[ProviderNamedReference] = Field(default_factory=list)
    rules: list[GatewayRouteRuleDetail] = Field(default_factory=list)
    parent_statuses: list[GatewayRouteParentStatusDetail] = Field(default_factory=list)
    conditions: list[ProviderCondition] = Field(default_factory=list)


ResourceProviderDetail = Annotated[
    AwsMachineProviderDetail
    | AwsManagedClusterProviderDetail
    | AwsManagedControlPlaneProviderDetail
    | AwsManagedMachinePoolProviderDetail
    | AzureMachineProviderDetail
    | AzureManagedControlPlaneProviderDetail
    | AzureManagedMachinePoolProviderDetail
    | CapiClusterProviderDetail
    | CapiKubeadmControlPlaneProviderDetail
    | CapiMachineDeploymentProviderDetail
    | CapiMachineHealthCheckProviderDetail
    | CapiMachinePoolProviderDetail
    | CapiMachineProviderDetail
    | CapiMachineSetProviderDetail
    | CertificateProviderDetail
    | CertificateRequestProviderDetail
    | ClusterComplianceReportProviderDetail
    | CrossplaneCompositeProviderDetail
    | CrossplaneManagedResourceProviderDetail
    | CronWorkflowProviderDetail
    | ExternalSecretProviderDetail
    | PersistentVolumeClaimProviderDetail
    | SealedSecretProviderDetail
    | SecretProviderDetail
    | SecretStoreProviderDetail
    | WorkflowProviderDetail
    | GatewayClassProviderDetail
    | GcpMachineProviderDetail
    | GcpManagedControlPlaneProviderDetail
    | GcpManagedMachinePoolProviderDetail
    | GrpcRouteProviderDetail
    | HttpRouteProviderDetail
    | JobProviderDetail
    | KarpenterEc2NodeClassProviderDetail
    | KarpenterNodeClaimProviderDetail
    | KarpenterNodePoolProviderDetail
    | KedaScaledObjectProviderDetail
    | KedaScaledJobProviderDetail
    | SbomReportProviderDetail
    | VulnerabilityReportProviderDetail
    | PrometheusRuleProviderDetail
    | TcpRouteProviderDetail
    | TlsRouteProviderDetail,
    Field(discriminator="type"),
]
