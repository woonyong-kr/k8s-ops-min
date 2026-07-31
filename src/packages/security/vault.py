from __future__ import annotations

import base64
import hashlib
import json
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request as urlrequest
from urllib.parse import parse_qs, quote

from packages.config.logs import get_logger
from packages.config.settings import env
from packages.contracts.security import SecretRef, SecretVaultPort, TokenVaultPort

SECRET_VAULT_PROVIDER_ENV = "SECRET_VAULT_PROVIDER"
TOKEN_VAULT_PROVIDER_ENV = "TOKEN_VAULT_PROVIDER"
SECRET_VAULT_AWS_REGION_ENV = "SECRET_VAULT_AWS_REGION"
K8S_SECRET_API_BASE_ENV = "KUBEHEAL_K8S_API_BASE"
K8S_SECRET_TOKEN_PATH_ENV = "KUBEHEAL_K8S_TOKEN_PATH"
K8S_SECRET_CA_CERT_PATH_ENV = "KUBEHEAL_K8S_CA_CERT_PATH"
K8S_SECRET_HTTP_TIMEOUT_SECONDS_ENV = "KUBEHEAL_K8S_SECRET_TIMEOUT_SECONDS"

PROVIDER_AUTO = "auto"
PROVIDER_ENV = "env"
PROVIDER_AWS_SECRETS_MANAGER = "aws-secrets-manager"
PROVIDER_KUBERNETES_SECRET = "kubernetes-secret"
ENV_REF_PREFIX = "env:"
AWS_SECRETS_MANAGER_REF_PREFIX = "aws-sm:"
KUBERNETES_SECRET_REF_PREFIX = "k8s-secret:"
DEFAULT_K8S_SECRET_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
DEFAULT_K8S_SECRET_CA_CERT_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
DEFAULT_K8S_SECRET_HTTP_TIMEOUT_SECONDS = "5"
AWS_PROVIDER_ALIASES = {
    PROVIDER_AWS_SECRETS_MANAGER,
    "aws",
    "aws-sm",
    "secretsmanager",
    "secrets-manager",
}
KUBERNETES_SECRET_PROVIDER_ALIASES = {
    PROVIDER_KUBERNETES_SECRET,
    "k8s",
    "k8s-secret",
    "kubernetes",
    "kubernetes-secret",
}

LOGGER = get_logger(__name__)


class SecretNotFound(RuntimeError):
    pass


class SecretProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedSecretRef:
    provider: str
    name: str
    field: str | None = None
    version_stage: str | None = None


class EnvSecretVault(SecretVaultPort):
    """개발/배포 공통 fallback: secret ref를 환경변수 이름으로 해석"""

    def read_secret(self, ref: SecretRef) -> str:
        parsed = parse_secret_ref(ref, default_provider=PROVIDER_ENV)
        if parsed.provider != PROVIDER_ENV:
            raise SecretNotFound(f"unsupported env secret ref provider: {parsed.provider}")
        value = env(parsed.name, "").strip()
        if not value:
            raise SecretNotFound(f"secret ref not found: {parsed.name}")
        log_secret_read(PROVIDER_ENV, parsed.name, parsed.field, parsed.version_stage)
        return value


class AwsSecretsManagerSecretVault(SecretVaultPort):
    """AWS Secrets Manager adapter.

    Ref 예시:
    - aws-sm:/my-app/prod/github-token
    - aws-sm:/my-app/prod/github#token
    - aws-sm:/my-app/prod/github?stage=AWSPREVIOUS#token
    """

    def __init__(self, client: Any | None = None, region_name: str | None = None) -> None:
        self._client = client
        self.region_name = region_name or env(SECRET_VAULT_AWS_REGION_ENV, "")

    def read_secret(self, ref: SecretRef) -> str:
        parsed = parse_secret_ref(ref, default_provider=PROVIDER_AWS_SECRETS_MANAGER)
        if parsed.provider != PROVIDER_AWS_SECRETS_MANAGER:
            raise SecretNotFound(f"unsupported aws secret ref provider: {parsed.provider}")
        request: dict[str, Any] = {"SecretId": parsed.name}
        if parsed.version_stage:
            request["VersionStage"] = parsed.version_stage
        try:
            response = self.client().get_secret_value(**request)
        except Exception as exc:
            raise SecretNotFound(f"aws secret ref not found: {redacted_ref(parsed.name)}") from exc
        value = secret_value_from_aws_response(response)
        if parsed.field:
            value = extract_json_field(value, parsed.field)
        log_secret_read(
            PROVIDER_AWS_SECRETS_MANAGER, parsed.name, parsed.field, parsed.version_stage
        )
        return value

    def client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise SecretProviderUnavailable(
                "boto3 is required for aws-sm secret refs; install the aws optional dependency"
            ) from exc
        kwargs = {"region_name": self.region_name} if self.region_name else {}
        self._client = boto3.client("secretsmanager", **kwargs)
        return self._client


class KubernetesSecretVault(SecretVaultPort):
    """클러스터 내 운영 배포용 Kubernetes Secret adapter.

    Ref 예시:
    - k8s-secret:management/github-app#token
    - k8s-secret:platform/llm-secrets#openai-api-key
    """

    def __init__(
        self,
        secret_reader: Any | None = None,
        api_base: str | None = None,
        token_path: str | None = None,
        ca_cert_path: str | None = None,
    ) -> None:
        self.secret_reader = secret_reader
        self.api_base = api_base
        self.token_path = token_path or env(
            K8S_SECRET_TOKEN_PATH_ENV, DEFAULT_K8S_SECRET_TOKEN_PATH
        )
        self.ca_cert_path = ca_cert_path or env(
            K8S_SECRET_CA_CERT_PATH_ENV, DEFAULT_K8S_SECRET_CA_CERT_PATH
        )

    def read_secret(self, ref: SecretRef) -> str:
        parsed = parse_secret_ref(ref, default_provider=PROVIDER_KUBERNETES_SECRET)
        if parsed.provider != PROVIDER_KUBERNETES_SECRET:
            raise SecretNotFound(f"unsupported kubernetes secret ref provider: {parsed.provider}")
        namespace, name = parse_kubernetes_secret_name(parsed.name)
        if not parsed.field:
            raise SecretNotFound("k8s-secret ref requires a key after '#', e.g. ns/name#token")
        secret = self.read_kubernetes_secret(namespace, name)
        value = secret_value_from_kubernetes_response(secret, parsed.field)
        log_secret_read(PROVIDER_KUBERNETES_SECRET, parsed.name, parsed.field, parsed.version_stage)
        return value

    def read_kubernetes_secret(self, namespace: str, name: str) -> dict[str, Any]:
        if self.secret_reader is not None:
            return dict(self.secret_reader(namespace, name))
        return self.fetch_kubernetes_secret(namespace, name)

    def fetch_kubernetes_secret(self, namespace: str, name: str) -> dict[str, Any]:
        api_base = self.kubernetes_api_base()
        token = self.service_account_token()
        url = (
            f"{api_base}/api/v1/namespaces/{quote(namespace, safe='')}"
            f"/secrets/{quote(name, safe='')}"
        )
        req = urlrequest.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        timeout = float(
            env(K8S_SECRET_HTTP_TIMEOUT_SECONDS_ENV, DEFAULT_K8S_SECRET_HTTP_TIMEOUT_SECONDS)
        )
        try:
            with urlrequest.urlopen(req, timeout=timeout, context=self.ssl_context()) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise SecretNotFound(
                f"k8s secret ref not found: {redacted_ref(f'{namespace}/{name}')}"
            ) from exc

    def kubernetes_api_base(self) -> str:
        configured = (self.api_base or env(K8S_SECRET_API_BASE_ENV, "")).strip().rstrip("/")
        if configured:
            return configured
        host = env("KUBERNETES_SERVICE_HOST", "").strip()
        port = env("KUBERNETES_SERVICE_PORT", "443").strip()
        if not host:
            raise SecretProviderUnavailable(
                f"{K8S_SECRET_API_BASE_ENV} or in-cluster Kubernetes service env is required"
            )
        return f"https://{host}:{port}"

    def ssl_context(self) -> ssl.SSLContext | None:
        ca_cert = Path(self.ca_cert_path)
        if ca_cert.exists():
            return ssl.create_default_context(cafile=str(ca_cert))
        return None

    def service_account_token(self) -> str:
        path = Path(self.token_path)
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SecretProviderUnavailable(
                f"kubernetes service account token not readable: {path}"
            ) from exc
        if not value:
            raise SecretProviderUnavailable(f"kubernetes service account token is empty: {path}")
        return value


class RoutingSecretVault(SecretVaultPort):
    """prefix 기반 vault 라우터.

    무접두사·env: ref 는 하위 호환을 위해 환경변수로, aws-sm: 은 AWS Secrets Manager 로,
    k8s-secret: 은 service account 신원의 Kubernetes API 로 해석
    """

    def __init__(
        self,
        env_vault: SecretVaultPort | None = None,
        aws_vault: SecretVaultPort | None = None,
        kubernetes_vault: SecretVaultPort | None = None,
    ) -> None:
        self.env_vault = env_vault or EnvSecretVault()
        self.aws_vault = aws_vault
        self.kubernetes_vault = kubernetes_vault

    def read_secret(self, ref: SecretRef) -> str:
        parsed = parse_secret_ref(ref, default_provider=PROVIDER_ENV)
        if parsed.provider == PROVIDER_AWS_SECRETS_MANAGER:
            if self.aws_vault is None:
                self.aws_vault = AwsSecretsManagerSecretVault()
            return self.aws_vault.read_secret(ref)
        if parsed.provider == PROVIDER_KUBERNETES_SECRET:
            if self.kubernetes_vault is None:
                self.kubernetes_vault = KubernetesSecretVault()
            return self.kubernetes_vault.read_secret(ref)
        return self.env_vault.read_secret(ref)


class EnvTokenVault(TokenVaultPort):
    def __init__(self, secret_vault: SecretVaultPort | None = None) -> None:
        self.secret_vault = secret_vault or EnvSecretVault()

    def read_token(self, ref: SecretRef) -> str:
        return self.secret_vault.read_secret(ref)


def build_secret_vault(provider: str | None = None) -> SecretVaultPort:
    selected = normalize_provider(provider or env(SECRET_VAULT_PROVIDER_ENV, PROVIDER_AUTO))
    if selected == PROVIDER_AUTO:
        return RoutingSecretVault()
    if selected == PROVIDER_ENV:
        return EnvSecretVault()
    if selected == PROVIDER_AWS_SECRETS_MANAGER:
        return AwsSecretsManagerSecretVault()
    if selected == PROVIDER_KUBERNETES_SECRET:
        return KubernetesSecretVault()
    raise ValueError(f"unsupported secret vault provider: {selected}")


def build_token_vault(provider: str | None = None) -> TokenVaultPort:
    selected = provider or env(TOKEN_VAULT_PROVIDER_ENV, "")
    secret_vault = build_secret_vault(selected or None)
    return EnvTokenVault(secret_vault)


def normalize_provider(value: str) -> str:
    provider = value.strip().lower() or PROVIDER_AUTO
    if provider in AWS_PROVIDER_ALIASES:
        return PROVIDER_AWS_SECRETS_MANAGER
    if provider in KUBERNETES_SECRET_PROVIDER_ALIASES:
        return PROVIDER_KUBERNETES_SECRET
    if provider in {PROVIDER_AUTO, PROVIDER_ENV}:
        return provider
    return provider


def parse_secret_ref(ref: SecretRef, *, default_provider: str) -> ParsedSecretRef:
    raw = ref.value.strip()
    if not raw:
        raise SecretNotFound("secret ref is empty")
    if raw.startswith(ENV_REF_PREFIX):
        return ParsedSecretRef(PROVIDER_ENV, raw.removeprefix(ENV_REF_PREFIX))
    if raw.startswith(AWS_SECRETS_MANAGER_REF_PREFIX):
        return parse_aws_secret_ref(raw.removeprefix(AWS_SECRETS_MANAGER_REF_PREFIX))
    if raw.startswith(KUBERNETES_SECRET_REF_PREFIX):
        return parse_kubernetes_secret_ref(raw.removeprefix(KUBERNETES_SECRET_REF_PREFIX))
    return ParsedSecretRef(normalize_provider(default_provider), raw)


def parse_aws_secret_ref(raw: str) -> ParsedSecretRef:
    locator, _, field = raw.partition("#")
    name, _, query = locator.partition("?")
    if not name:
        raise SecretNotFound("aws secret ref is empty")
    params = parse_qs(query, keep_blank_values=False)
    stage = first_query_value(params, "stage") or first_query_value(params, "version_stage")
    return ParsedSecretRef(
        PROVIDER_AWS_SECRETS_MANAGER,
        name,
        field.strip() or None,
        stage.strip() if stage else None,
    )


def first_query_value(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key) or []
    return values[0] if values else None


def parse_kubernetes_secret_ref(raw: str) -> ParsedSecretRef:
    locator, _, field = raw.partition("#")
    name, _, query = locator.partition("?")
    if not name:
        raise SecretNotFound("k8s-secret ref is empty")
    params = parse_qs(query, keep_blank_values=False)
    key = field.strip() or first_query_value(params, "key")
    return ParsedSecretRef(
        PROVIDER_KUBERNETES_SECRET,
        name.strip(),
        key.strip() if key else None,
    )


def parse_kubernetes_secret_name(name: str) -> tuple[str, str]:
    parts = [part for part in name.strip("/").split("/") if part]
    if len(parts) != 2:
        raise SecretNotFound("k8s-secret ref must be '<namespace>/<secret-name>#<key>'")
    return parts[0], parts[1]


def secret_value_from_aws_response(response: dict[str, Any]) -> str:
    if "SecretString" in response and response["SecretString"] is not None:
        return str(response["SecretString"])
    if "SecretBinary" in response and response["SecretBinary"] is not None:
        data = response["SecretBinary"]
        if isinstance(data, str):
            data = base64.b64decode(data)
        return bytes(data).decode("utf-8")
    raise SecretNotFound("aws secret response did not include SecretString or SecretBinary")


def secret_value_from_kubernetes_response(response: dict[str, Any], key: str) -> str:
    data = response.get("data")
    if not isinstance(data, dict) or key not in data:
        raise SecretNotFound(f"k8s secret data key not found: {key}")
    encoded = data[key]
    if not isinstance(encoded, str):
        raise SecretNotFound(f"k8s secret data key is not a string: {key}")
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except Exception as exc:
        raise SecretNotFound(f"k8s secret data key is not valid base64: {key}") from exc


def extract_json_field(secret: str, field: str) -> str:
    try:
        value: Any = json.loads(secret)
    except json.JSONDecodeError as exc:
        raise SecretNotFound(f"secret field requested but value is not JSON: {field}") from exc
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            raise SecretNotFound(f"secret field not found: {field}")
        value = value[part]
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def log_secret_read(provider: str, name: str, field: str | None, version_stage: str | None) -> None:
    LOGGER.info(
        "secret_vault_read",
        extra={
            "context": {
                "provider": provider,
                "ref_hash": redacted_ref(name),
                "field": bool(field),
                "version_stage": version_stage or "",
            }
        },
    )


def redacted_ref(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]
