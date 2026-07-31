from __future__ import annotations

import importlib
import pkgutil

from providers.base import ConfigReader, ProviderResult, TelemetryProvider

# provider 모듈 자동 발견 — import 시 @telemetry.source 데코레이터가 레지스트리에 등록.
# 새 소스 추가 = providers/ 아래 파일 1개(이 목록 수정 불필요).
for _info in pkgutil.iter_modules(__path__, f"{__name__}."):
    if not _info.name.endswith("_providers"):
        continue
    importlib.import_module(_info.name)

# 명시 재노출(기존 import 경로 호환)
from providers.kubernetes_providers import KubernetesSnapshotProvider  # noqa: E402
from providers.loki_providers import LokiLogsProvider  # noqa: E402
from providers.metadata_providers import MetadataProvider  # noqa: E402
from providers.prometheus_providers import PrometheusMetricsProvider  # noqa: E402
from providers.tempo_providers import TempoTracesProvider  # noqa: E402

__all__ = [
    "ConfigReader",
    "KubernetesSnapshotProvider",
    "LokiLogsProvider",
    "MetadataProvider",
    "PrometheusMetricsProvider",
    "ProviderResult",
    "TelemetryProvider",
    "TempoTracesProvider",
]
