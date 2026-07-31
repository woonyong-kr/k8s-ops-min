from __future__ import annotations

import os

import httpx

from packages.config.settings import env


class KubernetesApiConfig:
    SERVICE_HOST_ENV = "KUBERNETES_SERVICE_HOST"
    SERVICE_PORT_ENV = "KUBERNETES_SERVICE_PORT_HTTPS"
    SERVICE_ACCOUNT_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    SERVICE_ACCOUNT_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    HTTP_TIMEOUT_SECONDS_ENV = (
        "KUBERNETES_HTTP_TIMEOUT_SECONDS"  # k8s API HTTP 타임아웃 초(기본 20)
    )
    HTTP_TIMEOUT_SECONDS = int(env(HTTP_TIMEOUT_SECONDS_ENV, "20"))


def kubernetes_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    verify: str | bool = (
        KubernetesApiConfig.SERVICE_ACCOUNT_CA_PATH
        if os.path.exists(KubernetesApiConfig.SERVICE_ACCOUNT_CA_PATH)
        else True
    )
    return httpx.AsyncClient(
        verify=verify,
        transport=transport,
        timeout=KubernetesApiConfig.HTTP_TIMEOUT_SECONDS,
    )


def kubernetes_headers(token: str, content_type: str | None = None) -> dict[str, str]:
    headers = {"authorization": f"Bearer {token}"}
    if content_type is not None:
        headers["content-type"] = content_type
    return headers


def kubernetes_api_base_url() -> str | None:
    host = env(KubernetesApiConfig.SERVICE_HOST_ENV, "")
    port = env(KubernetesApiConfig.SERVICE_PORT_ENV, "443")
    return f"https://{host}:{port}" if host else None


def service_account_token() -> str | None:
    if not os.path.exists(KubernetesApiConfig.SERVICE_ACCOUNT_TOKEN_PATH):
        return None
    with open(KubernetesApiConfig.SERVICE_ACCOUNT_TOKEN_PATH, encoding="utf-8") as token_file:
        return token_file.read().strip()
