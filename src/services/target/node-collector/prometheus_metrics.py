from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class MetricSample:
    # Prometheus 샘플 1개 + HELP/TYPE 렌더용 메타데이터.
    # 새 metric 추가는 대부분 MetricSample 추가로 끝남.
    name: str
    help: str
    value: float | int
    labels: dict[str, str]
    type: Literal["gauge", "counter"] = "gauge"


def render_labels(labels: dict[str, str]) -> str:
    # label 은 metric 이름 뒤에 렌더됨:
    # metric_name{node="target-control-plane",runtime="containerd"} 1
    if not labels:
        return ""

    label_pairs = ",".join(f'{key}="{value}"' for key, value in labels.items())
    # 이중 중괄호는 f-string 안에서 Prometheus label 중괄호 리터럴 escape.
    return f"{{{label_pairs}}}"


def render_metric_sample(sample: MetricSample) -> list[str]:
    # Prometheus text exposition 은 샘플 앞에 HELP/TYPE 메타데이터 줄을 요구함.
    labels = render_labels(sample.labels)
    return [
        f"# HELP {sample.name} {sample.help}",
        f"# TYPE {sample.name} {sample.type}",
        f"{sample.name}{labels} {sample.value}",
    ]


def render_prometheus_metrics(samples: list[MetricSample]) -> str:
    lines = []
    for sample in samples:
        lines.extend(render_metric_sample(sample))

    lines.append("")
    return "\n".join(lines)
