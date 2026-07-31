"""Release-flow execution preview helpers.

The preview is intentionally deterministic and side-effect free. It does not
dispatch GitOps work; it explains which steps can run in each dependency wave
and which policy gates would be hit before an operator starts a real release.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject


def build_release_plan_preview(payload: Mapping[str, Any]) -> JsonObject:
    steps = [step for step in payload.get("steps", []) if isinstance(step, Mapping)]
    settings = mapping_value(payload.get("settings"))
    blockers = graph_blockers(steps)
    wave_by_app = dependency_waves(steps) if not blockers else {}
    preview_steps = [
        preview_step(step, index, wave_by_app.get(str(step.get("application_id") or "")), settings)
        for index, step in enumerate(steps)
    ]
    waves = grouped_waves(preview_steps)
    if not steps:
        blockers.append("No release steps are configured.")
    executable = not blockers
    return {
        "plan_id": payload.get("plan_id"),
        "executable": executable,
        "summary": preview_summary(len(steps), len(waves), blockers),
        "waves": waves,
        "steps": preview_steps,
        "blockers": blockers,
    }


def graph_blockers(steps: list[Mapping[str, Any]]) -> list[str]:
    blockers: list[str] = []
    ids = [str(step.get("application_id") or "") for step in steps]
    known = {app_id for app_id in ids if app_id}
    duplicates = sorted({app_id for app_id in known if ids.count(app_id) > 1})
    for app_id in duplicates:
        blockers.append(f"Application {app_id} appears more than once.")
    for step in steps:
        app_id = str(step.get("application_id") or "")
        if not app_id:
            blockers.append("A release step is missing application_id.")
            continue
        for dep in list_value(step.get("depends_on")):
            dep_id = str(dep)
            if dep_id == app_id:
                blockers.append(f"Application {app_id} depends on itself.")
            elif dep_id not in known:
                blockers.append(f"Application {app_id} depends on unknown step {dep_id}.")
    if not blockers and has_cycle({str(s.get("application_id") or ""): deps_for(s) for s in steps}):
        blockers.append("Release dependencies contain a cycle.")
    return blockers


def dependency_waves(steps: list[Mapping[str, Any]]) -> dict[str, int]:
    remaining = {str(step.get("application_id") or ""): set(deps_for(step)) for step in steps}
    completed: set[str] = set()
    wave_by_app: dict[str, int] = {}
    wave = 1
    while remaining:
        ready = sorted(app_id for app_id, deps in remaining.items() if deps <= completed)
        if not ready:
            return {}
        for app_id in ready:
            wave_by_app[app_id] = wave
            completed.add(app_id)
            remaining.pop(app_id, None)
        wave += 1
    return wave_by_app


def preview_step(
    step: Mapping[str, Any],
    index: int,
    wave: int | None,
    settings: Mapping[str, Any],
) -> JsonObject:
    config = mapping_value(step.get("config"))
    application_id = str(step.get("application_id") or "")
    environment = str(config.get("environment") or first_environment(settings) or "sandbox")
    gate = str(config.get("approval_gate") or "inherit")
    if gate == "inherit":
        gate = str(settings.get("approval_policy") or "auto_safe")
    strategy = str(config.get("strategy") or settings.get("default_strategy") or "rolling")
    return {
        "step_id": str(step.get("step_id") or f"preview-step-{index}"),
        "application_id": application_id,
        "name": str(step.get("name") or application_id),
        "position": int_like(step.get("position"), index),
        "wave": wave,
        "blocked_by": deps_for(step),
        "gate": gate,
        "strategy": strategy,
        "environment": environment,
        "action": str(config.get("execution_action") or "render_diff_apply"),
    }


def grouped_waves(steps: list[JsonObject]) -> list[JsonObject]:
    grouped: dict[int, list[JsonObject]] = {}
    for step in steps:
        wave = step.get("wave")
        if isinstance(wave, int):
            grouped.setdefault(wave, []).append(step)
    return [
        {
            "wave": wave,
            "step_ids": [str(step["step_id"]) for step in items],
            "applications": [str(step["application_id"]) for step in items],
        }
        for wave, items in sorted(grouped.items())
    ]


def preview_summary(step_count: int, wave_count: int, blockers: list[str]) -> str:
    if blockers:
        return f"{len(blockers)} blocker(s) must be resolved before execution."
    if step_count == 0:
        return "No release steps are configured."
    return f"{step_count} step(s) can run in {wave_count} dependency wave(s)."


def deps_for(step: Mapping[str, Any]) -> list[str]:
    return [str(dep) for dep in list_value(step.get("depends_on")) if dep]


def has_cycle(graph: Mapping[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(app_id: str) -> bool:
        if app_id in visiting:
            return True
        if app_id in visited:
            return False
        visiting.add(app_id)
        for dep in graph.get(app_id, []):
            if dep in graph and walk(dep):
                return True
        visiting.remove(app_id)
        visited.add(app_id)
        return False

    return any(walk(app_id) for app_id in graph)


def first_environment(settings: Mapping[str, Any]) -> str:
    order = list_value(settings.get("environment_order"))
    return str(order[0]) if order else ""


def mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def int_like(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
