from __future__ import annotations

from collections.abc import Mapping, Sequence


def render_prometheus_metrics(metrics: Mapping[str, float | int]) -> str:
    lines: list[str] = []
    for name, value in sorted(metrics.items()):
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


def render_labeled_counter(name: str, values: Mapping[str, int], label: str) -> str:
    return render_labeled_gauge(name, values, label)


def render_labeled_gauge(name: str, values: Mapping[str, float | int], label: str) -> str:
    lines = [f"# TYPE {name} gauge"]
    for key, value in sorted(values.items()):
        safe_key = str(key).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{name}{{{label}="{safe_key}"}} {value}')
    return "\n".join(lines) + "\n"


def render_multi_labeled_gauge(
    name: str,
    values: Mapping[tuple[str, ...], float | int],
    labels: Sequence[str],
) -> str:
    lines = [f"# TYPE {name} gauge"]
    for keys, value in sorted(values.items()):
        if len(keys) != len(labels):
            raise ValueError("metric label cardinality mismatch")
        label_values = []
        for label, key in zip(labels, keys, strict=True):
            safe_key = str(key).replace("\\", "\\\\").replace('"', '\\"')
            label_values.append(f'{label}="{safe_key}"')
        lines.append(f"{name}{{{','.join(label_values)}}} {value}")
    return "\n".join(lines) + "\n"
