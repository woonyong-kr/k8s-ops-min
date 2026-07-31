"""Kubernetes node metadata 기반 cloud provider 감지."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

PROVIDER_ID_PREFIXES = (
    ("aws://", "eks"),
    ("azure://", "aks"),
    ("gce://", "gke"),
)
PROVIDER_LABEL_PREFIXES = (
    ("eks.amazonaws.com/", "eks"),
    ("alpha.eksctl.io/", "eks"),
    ("cloud.google.com/gke-", "gke"),
    ("iam.gke.io/", "gke"),
    ("kubernetes.azure.com/", "aks"),
)
DETECTED_PROVIDERS = frozenset({"eks", "gke", "aks"})


def detect_kubernetes_provider(nodes: Iterable[Mapping[str, Any]]) -> str | None:
    """Node providerID를 우선하고 vendor 전용 label만 보조 신호로 사용한다."""
    rows = list(nodes)
    for node in rows:
        provider_id = _text(_mapping(node.get("spec")).get("providerID")).lower()
        for prefix, provider in PROVIDER_ID_PREFIXES:
            if provider_id.startswith(prefix):
                return provider

    for node in rows:
        labels = _mapping(_mapping(node.get("metadata")).get("labels"))
        for key in labels:
            normalized = _text(key).lower()
            for prefix, provider in PROVIDER_LABEL_PREFIXES:
                if normalized.startswith(prefix):
                    return provider
    return None


def normalized_detected_provider(value: object) -> str | None:
    provider = _text(value).lower()
    return provider if provider in DETECTED_PROVIDERS else None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
