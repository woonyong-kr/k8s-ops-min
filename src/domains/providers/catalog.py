from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from packages.config.settings import env


class ProviderCategory(StrEnum):
    SOURCE = "source"
    DEPLOY = "deploy"
    CLOUD = "cloud"
    SECRET = "secret"


class ProviderStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


PROVIDER_DISABLED_ENV = "KUBEHEAL_DISABLED_PROVIDERS"
EXISTING_K8S_PROVIDER = "existing-k8s"
PLURAL_PROVIDER = "plural"
EXTERNAL_CONSOLE_PROVIDER = "external-console"
EKS_PROVIDER = "eks"
GKE_PROVIDER = "gke"
AKS_PROVIDER = "aks"
KIND_PROVIDER = "kind"
MINIKUBE_PROVIDER = "minikube"
MANUAL_MANIFEST_DEPLOY_PROVIDER = "manual-manifest"
KUBE_CONTEXT_DEPLOY_PROVIDER = "kube-context"
KUBE_CONTEXT_ALLOWLIST_ENV = "KUBE_CONTEXT_ALLOWLIST"
CLUSTER_CONTEXTS_ENV = "CLUSTER_CONTEXTS"
MGMT_CONTEXT_ENV = "MGMT_CONTEXT"
TARGET_CONTEXT_ENV = "TARGET_CONTEXT"
PLURAL_CLUSTER_HANDLES_ENV = "PLURAL_CLUSTER_HANDLES"
PLURAL_CONSOLE_URL_ENV = "PLURAL_CONSOLE_URL"
PLURAL_CLOUD_INSTANCES_ENV = "PLURAL_CLOUD_INSTANCES"
EXTERNAL_CLUSTER_HANDLES_ENV = "EXTERNAL_CLUSTER_HANDLES"
EXTERNAL_CONSOLE_URL_ENV = "EXTERNAL_CONSOLE_URL"
EXTERNAL_CONSOLE_INSTANCES_ENV = "EXTERNAL_CONSOLE_INSTANCES"


@dataclass(frozen=True)
class CredentialRequirement:
    key: str
    ref_prefixes: tuple[str, ...]
    required_for: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""

    def to_body(self) -> dict[str, object]:
        return {
            "key": self.key,
            "ref_prefixes": list(self.ref_prefixes),
            "required_for": list(self.required_for),
            "description": self.description,
        }


@dataclass(frozen=True)
class ProviderConfigField:
    key: str
    label: str
    required: bool = False
    kind: str = "text"
    options: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""

    def to_body(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "required": self.required,
            "kind": self.kind,
            "options": list(self.options),
            "description": self.description,
        }


@dataclass(frozen=True)
class ProviderDefinition:
    category: ProviderCategory
    key: str
    label: str
    status: ProviderStatus
    adapter: str | None
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    credential_requirements: tuple[CredentialRequirement, ...] = field(default_factory=tuple)
    config_keys: tuple[str, ...] = field(default_factory=tuple)
    config_fields: tuple[ProviderConfigField, ...] = field(default_factory=tuple)
    unavailable_reason: str | None = None

    def to_body(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "key": self.key,
            "label": self.label,
            "status": self.status.value,
            "adapter": self.adapter,
            "capabilities": list(self.capabilities),
            "credential_requirements": [
                requirement.to_body() for requirement in self.credential_requirements
            ],
            "config_keys": list(self.config_keys),
            "config_fields": [field.to_body() for field in self.config_fields],
            "unavailable_reason": self.unavailable_reason,
        }


CATALOG: tuple[ProviderDefinition, ...] = (
    ProviderDefinition(
        category=ProviderCategory.SOURCE,
        key="github",
        label="GitHub",
        status=ProviderStatus.AVAILABLE,
        adapter="GithubScmProvider + GitHub Contents API",
        capabilities=("webhook", "poll", "manifest_read", "safe_pr"),
        credential_requirements=(
            CredentialRequirement(
                key="github_token",
                ref_prefixes=("env:", "k8s-secret:", "aws-sm:"),
                required_for=("private_repo", "safe_pr"),
                description="GitHub API token or GitHub App installation token secret ref.",
            ),
        ),
        config_keys=("GITHUB_TOKEN_REF", "GITHUB_API_BASE", "SCM_REPO", "SCM_BASE_BRANCH"),
    ),
    ProviderDefinition(
        category=ProviderCategory.SOURCE,
        key="git-url",
        label="Generic Git URL",
        status=ProviderStatus.UNAVAILABLE,
        adapter=None,
        capabilities=("manifest_read",),
        unavailable_reason=(
            "checkout cache can mirror repos internally, but per-repository credential "
            "binding and allowlist are unavailable in this build"
        ),
    ),
    ProviderDefinition(
        category=ProviderCategory.SOURCE,
        key="gitlab",
        label="GitLab",
        status=ProviderStatus.UNAVAILABLE,
        adapter=None,
        unavailable_reason="GitLab webhook, contents, and merge request adapters are unavailable",
    ),
    ProviderDefinition(
        category=ProviderCategory.SOURCE,
        key="bitbucket",
        label="Bitbucket",
        status=ProviderStatus.UNAVAILABLE,
        adapter=None,
        unavailable_reason="Bitbucket webhook, contents, and pull request adapters are unavailable",
    ),
    ProviderDefinition(
        category=ProviderCategory.DEPLOY,
        key="manual-manifest",
        label="Manual Manifest Export",
        status=ProviderStatus.AVAILABLE,
        adapter="POST /targets apply=false",
        capabilities=("preview", "download_manifest"),
    ),
    ProviderDefinition(
        category=ProviderCategory.DEPLOY,
        key="kube-context",
        label="Kubernetes Context Apply",
        status=ProviderStatus.AVAILABLE,
        adapter="kubectl apply with KUBE_CONTEXT_ALLOWLIST",
        capabilities=("preview", "server_apply"),
        config_keys=("KUBE_CONTEXT_ALLOWLIST",),
    ),
    ProviderDefinition(
        category=ProviderCategory.DEPLOY,
        key="gitops-controller",
        label="External GitOps Controller",
        status=ProviderStatus.UNAVAILABLE,
        adapter=None,
        unavailable_reason=(
            "External Argo CD or Flux adapter is unavailable; "
            "the built-in workflow-controller remains active"
        ),
    ),
    ProviderDefinition(
        category=ProviderCategory.DEPLOY,
        key="jenkins",
        label="Jenkins",
        status=ProviderStatus.UNAVAILABLE,
        adapter=None,
        unavailable_reason="Jenkins job trigger/status adapter is unavailable",
    ),
    ProviderDefinition(
        category=ProviderCategory.CLOUD,
        key=EXISTING_K8S_PROVIDER,
        label="Existing Kubernetes",
        status=ProviderStatus.AVAILABLE,
        adapter="kubeconfig context or target agent bootstrap",
        capabilities=("install_target_agent", "apply_manifest"),
        config_keys=(KUBE_CONTEXT_ALLOWLIST_ENV,),
        config_fields=(
            ProviderConfigField(
                key="context_name",
                label="Kube context",
                description="kubectl --context 에 사용할 kubeconfig context 이름.",
            ),
        ),
    ),
    ProviderDefinition(
        category=ProviderCategory.CLOUD,
        key=EKS_PROVIDER,
        label="Amazon EKS",
        status=ProviderStatus.AVAILABLE,
        adapter="aws eks update-kubeconfig + manual manifest bootstrap",
        capabilities=("install_target_agent", "bootstrap_command"),
        config_fields=(
            ProviderConfigField(key="region", label="AWS region", required=True),
            ProviderConfigField(key="eks_cluster_name", label="EKS cluster name", required=True),
            ProviderConfigField(
                key="context_alias",
                label="Context alias",
                description="생략하면 등록 cluster_id를 alias로 사용.",
            ),
        ),
    ),
    ProviderDefinition(
        category=ProviderCategory.CLOUD,
        key=GKE_PROVIDER,
        label="Google Kubernetes Engine",
        status=ProviderStatus.AVAILABLE,
        adapter="gcloud container clusters get-credentials + manual manifest bootstrap",
        capabilities=("install_target_agent", "bootstrap_command"),
        config_fields=(
            ProviderConfigField(key="project_id", label="GCP project ID", required=True),
            ProviderConfigField(
                key="location_type",
                label="Location type",
                required=True,
                kind="select",
                options=("region", "zone"),
            ),
            ProviderConfigField(key="location", label="Region or zone", required=True),
            ProviderConfigField(key="gke_cluster_name", label="GKE cluster name", required=True),
        ),
    ),
    ProviderDefinition(
        category=ProviderCategory.CLOUD,
        key=AKS_PROVIDER,
        label="Azure Kubernetes Service",
        status=ProviderStatus.AVAILABLE,
        adapter="az aks get-credentials + manual manifest bootstrap",
        capabilities=("install_target_agent", "bootstrap_command"),
        config_fields=(
            ProviderConfigField(key="resource_group", label="Resource group", required=True),
            ProviderConfigField(key="aks_cluster_name", label="AKS cluster name", required=True),
        ),
    ),
    ProviderDefinition(
        category=ProviderCategory.CLOUD,
        key=KIND_PROVIDER,
        label="kind",
        status=ProviderStatus.AVAILABLE,
        adapter="kubectl context kind-<cluster> + manual manifest bootstrap",
        capabilities=("install_target_agent", "bootstrap_command", "developer_loop"),
        config_fields=(
            ProviderConfigField(
                key="kind_cluster_name",
                label="kind cluster name",
                description="생략하면 등록 name 또는 cluster_id를 사용.",
            ),
        ),
    ),
    ProviderDefinition(
        category=ProviderCategory.CLOUD,
        key=MINIKUBE_PROVIDER,
        label="minikube",
        status=ProviderStatus.AVAILABLE,
        adapter="kubectl context/profile + manual manifest bootstrap",
        capabilities=("install_target_agent", "bootstrap_command", "developer_loop"),
        config_fields=(
            ProviderConfigField(
                key="profile",
                label="minikube profile",
                description="생략하면 minikube context를 사용.",
            ),
        ),
    ),
    ProviderDefinition(
        category=ProviderCategory.CLOUD,
        key=PLURAL_PROVIDER,
        label="Plural",
        status=ProviderStatus.UNAVAILABLE,
        adapter="Plural Console metadata + target agent bootstrap",
        capabilities=("import_handles", "install_target_agent", "apply_manifest"),
        config_keys=(
            PLURAL_CONSOLE_URL_ENV,
            PLURAL_CLUSTER_HANDLES_ENV,
            PLURAL_CLOUD_INSTANCES_ENV,
            KUBE_CONTEXT_ALLOWLIST_ENV,
        ),
        unavailable_reason="Plural console metadata is not configured",
    ),
    ProviderDefinition(
        category=ProviderCategory.CLOUD,
        key=EXTERNAL_CONSOLE_PROVIDER,
        label="External Console",
        status=ProviderStatus.UNAVAILABLE,
        adapter="external console metadata + target agent bootstrap",
        capabilities=("import_handles", "install_target_agent", "apply_manifest"),
        config_keys=(
            EXTERNAL_CONSOLE_URL_ENV,
            EXTERNAL_CLUSTER_HANDLES_ENV,
            EXTERNAL_CONSOLE_INSTANCES_ENV,
            KUBE_CONTEXT_ALLOWLIST_ENV,
        ),
        unavailable_reason="external console metadata is not configured",
    ),
    ProviderDefinition(
        category=ProviderCategory.CLOUD,
        key="local",
        label="Local Kubernetes",
        status=ProviderStatus.AVAILABLE,
        adapter="scripts/up.sh",
        capabilities=("kind", "minikube", "developer_loop"),
    ),
    ProviderDefinition(
        category=ProviderCategory.CLOUD,
        key="aws",
        label="AWS",
        status=ProviderStatus.AVAILABLE,
        adapter="scripts/aws-up.sh + AWS credential chain",
        capabilities=("eks", "ecr", "manual_deploy"),
        credential_requirements=(
            CredentialRequirement(
                key="aws_credentials",
                ref_prefixes=("aws-profile:", "env:"),
                required_for=("deploy",),
                description="AWS profile or environment credential chain for manual deployments.",
            ),
        ),
        config_keys=("AWS_REGION", "AWS_PROFILE", "ECR_REPO"),
    ),
    ProviderDefinition(
        category=ProviderCategory.CLOUD,
        key="gcp",
        label="Google Cloud",
        status=ProviderStatus.UNAVAILABLE,
        adapter=None,
        unavailable_reason="GKE, Artifact Registry, and workload identity adapters are unavailable",
    ),
    ProviderDefinition(
        category=ProviderCategory.CLOUD,
        key="azure",
        label="Azure",
        status=ProviderStatus.UNAVAILABLE,
        adapter=None,
        unavailable_reason="AKS, ACR, and workload identity adapters are unavailable",
    ),
    ProviderDefinition(
        category=ProviderCategory.SECRET,
        key="env",
        label="Environment Variable",
        status=ProviderStatus.AVAILABLE,
        adapter="EnvSecretVault",
        capabilities=("local", "ci"),
        config_keys=("SECRET_VAULT_PROVIDER", "TOKEN_VAULT_PROVIDER"),
    ),
    ProviderDefinition(
        category=ProviderCategory.SECRET,
        key="k8s-secret",
        label="Kubernetes Secret",
        status=ProviderStatus.AVAILABLE,
        adapter="KubernetesSecretVault",
        capabilities=("in_cluster", "namespaced_secret"),
        config_keys=("KUBERNETES_SERVICE_HOST", "KUBEHEAL_K8S_API_BASE"),
    ),
    ProviderDefinition(
        category=ProviderCategory.SECRET,
        key="aws-sm",
        label="AWS Secrets Manager",
        status=ProviderStatus.AVAILABLE,
        adapter="AwsSecretsManagerSecretVault",
        capabilities=("managed_secret", "rotation_stage"),
        config_keys=("SECRET_VAULT_AWS_REGION",),
    ),
    ProviderDefinition(
        category=ProviderCategory.SECRET,
        key="vault",
        label="HashiCorp Vault",
        status=ProviderStatus.UNAVAILABLE,
        adapter=None,
        unavailable_reason="HashiCorp Vault adapter is unavailable",
    ),
    ProviderDefinition(
        category=ProviderCategory.SECRET,
        key="gcp-sm",
        label="Google Secret Manager",
        status=ProviderStatus.UNAVAILABLE,
        adapter=None,
        unavailable_reason="Google Secret Manager adapter is unavailable",
    ),
)


class ProviderUnavailable(ValueError):
    pass


class UnknownProvider(ValueError):
    pass


WORD_SPLIT_RE = re.compile(r"[\s,]+")
CLUSTER_ID_CHARS_RE = re.compile(r"[^a-z0-9-]+")
REGISTRATION_CLOUD_PROVIDERS = (
    EXISTING_K8S_PROVIDER,
    EKS_PROVIDER,
    GKE_PROVIDER,
    AKS_PROVIDER,
    KIND_PROVIDER,
    MINIKUBE_PROVIDER,
    PLURAL_PROVIDER,
    EXTERNAL_CONSOLE_PROVIDER,
)


def provider_catalog() -> tuple[ProviderDefinition, ...]:
    catalog = tuple(runtime_provider_definition(definition) for definition in CATALOG)
    disabled = disabled_provider_keys()
    if not disabled:
        return catalog
    return tuple(
        definition
        if provider_key(definition.category, definition.key) not in disabled
        else ProviderDefinition(
            category=definition.category,
            key=definition.key,
            label=definition.label,
            status=ProviderStatus.UNAVAILABLE,
            adapter=definition.adapter,
            capabilities=definition.capabilities,
            credential_requirements=definition.credential_requirements,
            config_keys=definition.config_keys,
            config_fields=definition.config_fields,
            unavailable_reason="disabled by KUBEHEAL_DISABLED_PROVIDERS",
        )
        for definition in catalog
    )


def runtime_provider_definition(definition: ProviderDefinition) -> ProviderDefinition:
    if definition.category != ProviderCategory.CLOUD:
        return definition
    if definition.key == PLURAL_PROVIDER and plural_metadata_configured():
        return definition_available(definition)
    if definition.key == EXTERNAL_CONSOLE_PROVIDER and external_console_metadata_configured():
        return definition_available(definition)
    return definition


def definition_available(definition: ProviderDefinition) -> ProviderDefinition:
    return ProviderDefinition(
        category=definition.category,
        key=definition.key,
        label=definition.label,
        status=ProviderStatus.AVAILABLE,
        adapter=definition.adapter,
        capabilities=definition.capabilities,
        credential_requirements=definition.credential_requirements,
        config_keys=definition.config_keys,
        config_fields=definition.config_fields,
        unavailable_reason=None,
    )


def cluster_registration_discovery() -> dict[str, object]:
    flows: list[dict[str, object]] = []
    candidates_by_provider = {
        EXISTING_K8S_PROVIDER: existing_k8s_import_candidates(),
        PLURAL_PROVIDER: plural_import_candidates(),
        EXTERNAL_CONSOLE_PROVIDER: external_console_import_candidates(),
    }
    deploy_provider_bodies = {
        definition.key: definition.to_body()
        for definition in provider_catalog()
        if definition.category == ProviderCategory.DEPLOY
        and definition.key in {MANUAL_MANIFEST_DEPLOY_PROVIDER, KUBE_CONTEXT_DEPLOY_PROVIDER}
    }

    for cloud_provider in REGISTRATION_CLOUD_PROVIDERS:
        definition = get_provider(ProviderCategory.CLOUD, cloud_provider)
        candidates = candidates_by_provider.get(cloud_provider, [])
        deploy_keys = registration_deploy_providers(candidates)
        flows.append(
            {
                "cloud_provider": cloud_provider,
                "label": definition.label,
                "status": definition.status.value,
                "description": registration_flow_description(cloud_provider),
                "deploy_providers": [
                    deploy_provider_bodies[key]
                    for key in deploy_keys
                    if key in deploy_provider_bodies
                ],
                "default_deploy_provider": deploy_keys[0],
                "supports_import": cloud_provider in candidates_by_provider,
                "unavailable_reason": definition.unavailable_reason,
                "import_candidates": candidates,
            }
        )

    all_candidates = [
        candidate for candidates in candidates_by_provider.values() for candidate in candidates
    ]
    return {
        "default_cloud_provider": EXISTING_K8S_PROVIDER,
        "default_deploy_provider": MANUAL_MANIFEST_DEPLOY_PROVIDER,
        "flows": flows,
        "import_candidates": all_candidates,
    }


def registration_deploy_providers(candidates: list[dict[str, object]]) -> list[str]:
    deploy_keys = [MANUAL_MANIFEST_DEPLOY_PROVIDER]
    if any(candidate.get("direct_apply_available") for candidate in candidates):
        deploy_keys.append(KUBE_CONTEXT_DEPLOY_PROVIDER)
    return deploy_keys


def registration_flow_description(cloud_provider: str) -> str:
    if cloud_provider == EKS_PROVIDER:
        return "Generate an AWS EKS kubeconfig bootstrap command for the target agent."
    if cloud_provider == GKE_PROVIDER:
        return "Generate a GKE get-credentials bootstrap command for the target agent."
    if cloud_provider == AKS_PROVIDER:
        return "Generate an AKS get-credentials bootstrap command for the target agent."
    if cloud_provider == KIND_PROVIDER:
        return "Register a kind cluster by applying the target agent manifest."
    if cloud_provider == MINIKUBE_PROVIDER:
        return "Register a minikube cluster by applying the target agent manifest."
    if cloud_provider == PLURAL_PROVIDER:
        return "Import Plural cluster handles from env, then bootstrap the target agent."
    if cloud_provider == EXTERNAL_CONSOLE_PROVIDER:
        return "Import external console cluster handles from env, then bootstrap the target agent."
    return "Register an existing Kubernetes cluster by installing the target agent."


def existing_k8s_import_candidates() -> list[dict[str, object]]:
    contexts = env_values_with_source(
        (
            CLUSTER_CONTEXTS_ENV,
            TARGET_CONTEXT_ENV,
            MGMT_CONTEXT_ENV,
            KUBE_CONTEXT_ALLOWLIST_ENV,
        )
    )
    return [
        import_candidate(
            cloud_provider=EXISTING_K8S_PROVIDER,
            name=context,
            source=f"env:{source}",
            kube_context=context,
            labels={"kube_context": context},
        )
        for context, source in contexts
    ]


def plural_import_candidates() -> list[dict[str, object]]:
    candidates = handle_import_candidates(
        cloud_provider=PLURAL_PROVIDER,
        handles=env_words(PLURAL_CLUSTER_HANDLES_ENV),
        source=f"env:{PLURAL_CLUSTER_HANDLES_ENV}",
        console_url=non_secret_env(PLURAL_CONSOLE_URL_ENV),
    )
    for instance_id in env_words(PLURAL_CLOUD_INSTANCES_ENV):
        suffix = env_suffix(instance_id)
        prefix = f"PLURAL_CLOUD_{suffix}"
        labels = instance_labels(
            instance_id,
            prefix,
            ("NAME", "OWNER", "PROVIDER", "REGION", "HOSTING", "SIZE"),
        )
        candidates.extend(
            handle_import_candidates(
                cloud_provider=PLURAL_PROVIDER,
                handles=env_words(f"{prefix}_CLUSTER_HANDLES"),
                source=f"env:{prefix}_CLUSTER_HANDLES",
                console_url=non_secret_env(f"{prefix}_CONSOLE_URL"),
                labels=labels,
            )
        )
    return dedupe_candidates(candidates)


def external_console_import_candidates() -> list[dict[str, object]]:
    candidates = handle_import_candidates(
        cloud_provider=EXTERNAL_CONSOLE_PROVIDER,
        handles=env_words(EXTERNAL_CLUSTER_HANDLES_ENV),
        source=f"env:{EXTERNAL_CLUSTER_HANDLES_ENV}",
        console_url=non_secret_env(EXTERNAL_CONSOLE_URL_ENV),
    )
    for instance_id in env_words(EXTERNAL_CONSOLE_INSTANCES_ENV):
        suffix = env_suffix(instance_id)
        prefix = f"EXTERNAL_CONSOLE_{suffix}"
        labels = instance_labels(
            instance_id,
            prefix,
            ("NAME", "OWNER", "PROVIDER", "REGION", "HOSTING", "SIZE"),
        )
        candidates.extend(
            handle_import_candidates(
                cloud_provider=EXTERNAL_CONSOLE_PROVIDER,
                handles=env_words(f"{prefix}_CLUSTER_HANDLES"),
                source=f"env:{prefix}_CLUSTER_HANDLES",
                console_url=non_secret_env(f"{prefix}_CONSOLE_URL"),
                labels=labels,
            )
        )
    return dedupe_candidates(candidates)


def handle_import_candidates(
    *,
    cloud_provider: str,
    handles: list[str],
    source: str,
    console_url: str | None = None,
    labels: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    contexts = env_words(CLUSTER_CONTEXTS_ENV)
    candidates: list[dict[str, object]] = []
    for index, handle in enumerate(handles):
        clean_handle = handle.removeprefix("@")
        kube_context = matching_context(clean_handle, contexts, index)
        candidates.append(
            import_candidate(
                cloud_provider=cloud_provider,
                name=clean_handle,
                source=source,
                kube_context=kube_context,
                external_handle=clean_handle,
                console_url=console_url,
                labels={
                    **(labels or {}),
                    "handle": clean_handle,
                    **({"kube_context": kube_context} if kube_context else {}),
                },
            )
        )
    return candidates


def import_candidate(
    *,
    cloud_provider: str,
    name: str,
    source: str,
    kube_context: str | None = None,
    external_handle: str | None = None,
    console_url: str | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, object]:
    direct_apply_available = is_direct_apply_available(kube_context)
    return {
        "cluster_id": slugify_cluster_id(external_handle or kube_context or name),
        "name": name,
        "source": source,
        "cloud_provider": cloud_provider,
        "deploy_provider": (
            KUBE_CONTEXT_DEPLOY_PROVIDER
            if direct_apply_available
            else MANUAL_MANIFEST_DEPLOY_PROVIDER
        ),
        "kube_context": kube_context,
        "external_handle": external_handle,
        "console_url": console_url,
        "direct_apply_available": direct_apply_available,
        "labels": {key: value for key, value in (labels or {}).items() if value},
    }


def env_values_with_source(names: tuple[str, ...]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in names:
        for value in env_words(name):
            if value not in seen:
                values.append((value, name))
                seen.add(value)
    return values


def env_words(name: str) -> list[str]:
    raw = env(name, "").strip().strip("\"'")
    if not raw:
        return []
    return [item.strip().strip("\"'") for item in WORD_SPLIT_RE.split(raw) if item.strip()]


def non_secret_env(name: str) -> str | None:
    value = env(name, "").strip().strip("\"'")
    return value or None


def instance_labels(
    instance_id: str,
    prefix: str,
    metadata_keys: tuple[str, ...],
) -> dict[str, str]:
    labels = {"instance": instance_id}
    for key in metadata_keys:
        value = non_secret_env(f"{prefix}_{key}")
        if value:
            labels[key.lower()] = value
    return labels


def matching_context(handle: str, contexts: list[str], index: int) -> str | None:
    if handle in contexts:
        return handle
    if index < len(contexts):
        return contexts[index]
    return None


def is_direct_apply_available(kube_context: str | None) -> bool:
    if not kube_context:
        return False
    return kube_context in set(env_words(KUBE_CONTEXT_ALLOWLIST_ENV))


def dedupe_candidates(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    seen: set[tuple[object, object, object]] = set()
    for candidate in candidates:
        key = (
            candidate.get("cloud_provider"),
            candidate.get("cluster_id"),
            candidate.get("kube_context") or candidate.get("external_handle"),
        )
        if key in seen:
            continue
        deduped.append(candidate)
        seen.add(key)
    return deduped


def slugify_cluster_id(value: str) -> str:
    slug = CLUSTER_ID_CHARS_RE.sub("-", value.removeprefix("@").lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "cluster"


def env_suffix(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()


def plural_metadata_configured() -> bool:
    return bool(
        env_words(PLURAL_CLUSTER_HANDLES_ENV)
        or env_words(PLURAL_CLOUD_INSTANCES_ENV)
        or non_secret_env(PLURAL_CONSOLE_URL_ENV)
    )


def external_console_metadata_configured() -> bool:
    return bool(
        env_words(EXTERNAL_CLUSTER_HANDLES_ENV)
        or env_words(EXTERNAL_CONSOLE_INSTANCES_ENV)
        or non_secret_env(EXTERNAL_CONSOLE_URL_ENV)
    )


def catalog_body() -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {
        category.value: [] for category in ProviderCategory
    }
    for definition in provider_catalog():
        grouped[definition.category.value].append(definition.to_body())
    return grouped


def get_provider(category: ProviderCategory | str, key: str) -> ProviderDefinition:
    normalized_category = ProviderCategory(str(category))
    normalized_key = normalize_key(key)
    for definition in provider_catalog():
        if definition.category == normalized_category and definition.key == normalized_key:
            return definition
    raise UnknownProvider(
        f"unknown {normalized_category.value} provider: {key}; supported: "
        f"{', '.join(provider_keys_for_category(normalized_category))}"
    )


def require_available_provider(category: ProviderCategory | str, key: str) -> ProviderDefinition:
    definition = get_provider(category, key)
    if definition.status != ProviderStatus.AVAILABLE:
        reason = definition.unavailable_reason or "provider adapter is not available"
        raise ProviderUnavailable(
            f"{definition.category.value} provider '{definition.key}' unavailable: {reason}"
        )
    return definition


def validate_provider_selection(
    selection: dict[str, str | None],
    *,
    credential_refs: dict[str, str] | None = None,
    capabilities: tuple[str, ...] = (),
) -> dict[str, object]:
    refs = credential_refs or {}
    errors: list[str] = []
    warnings: list[str] = []
    selected: dict[str, dict[str, object]] = {}

    for category in ProviderCategory:
        key = selection.get(category.value)
        if not key:
            continue
        try:
            definition = require_available_provider(category, key)
        except (ProviderUnavailable, UnknownProvider) as exc:
            errors.append(str(exc))
            continue
        selected[category.value] = definition.to_body()
        warnings.extend(credential_warnings(definition, refs, capabilities))

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "selected": selected,
    }


def credential_warnings(
    definition: ProviderDefinition, refs: dict[str, str], capabilities: tuple[str, ...]
) -> list[str]:
    warnings: list[str] = []
    requested = set(capabilities)
    for requirement in definition.credential_requirements:
        if requirement.required_for and not requested.intersection(requirement.required_for):
            continue
        ref = refs.get(requirement.key)
        if not ref:
            warnings.append(
                f"{definition.category.value} provider '{definition.key}' needs credential_ref "
                f"'{requirement.key}' for {', '.join(requirement.required_for)}"
            )
            continue
        if not ref.startswith(requirement.ref_prefixes):
            warnings.append(
                f"credential_ref '{requirement.key}' for provider '{definition.key}' must start with "
                f"{', '.join(requirement.ref_prefixes)}"
            )
    return warnings


def provider_keys_for_category(category: ProviderCategory) -> tuple[str, ...]:
    return tuple(
        definition.key for definition in provider_catalog() if definition.category == category
    )


def disabled_provider_keys() -> set[str]:
    raw = env(PROVIDER_DISABLED_ENV, "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def provider_key(category: ProviderCategory, key: str) -> str:
    return f"{category.value}:{normalize_key(key)}"


def normalize_key(key: str) -> str:
    return key.strip().lower().replace("_", "-")
