from __future__ import annotations

import re
from pathlib import Path

from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.realtime import BROWSER_LIVE_PATH

MATRIX_PATH = (
    Path(__file__).parents[1]
    / "frontend"
    / "src"
    / "devpreview"
    / "realtimeContractMatrix.ts"
)
FRONTEND_SRC = MATRIX_PATH.parents[1]
PRODUCTION_ENTRY = FRONTEND_SRC / "devpreview-unified.tsx"

RUNTIME_IMPORT_PATTERN = re.compile(
    r'import\s+(?!type\b)(?:(?!;).)*?(?:\s+from\s+)?'
    r'["\']([^"\']+)["\']\s*;',
    re.DOTALL,
)


def _resolve_typescript_import(source: Path, specifier: str) -> Path | None:
    if not specifier.startswith("."):
        return None
    base = source.parent / specifier
    candidates = (
        Path(f"{base}.ts"),
        Path(f"{base}.tsx"),
        base / "index.ts",
        base / "index.tsx",
    )
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def _production_api_modules() -> set[str]:
    root = FRONTEND_SRC.resolve()
    pending = [PRODUCTION_ENTRY.resolve()]
    visited: set[Path] = set()
    modules: set[str] = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        for match in RUNTIME_IMPORT_PATTERN.finditer(path.read_text(encoding="utf-8")):
            target = _resolve_typescript_import(path, match.group(1))
            if target is None or root not in target.parents:
                continue
            relative = target.relative_to(root)
            if relative.parts[0] == "api":
                module = relative.stem
                if module not in {"client", "schemas"} and not module.endswith("-schemas"):
                    modules.add(module)
                continue
            pending.append(target)
    return modules


def test_every_frontend_matrix_endpoint_is_a_canonical_backend_contract() -> None:
    source = MATRIX_PATH.read_text(encoding="utf-8")
    declared = set(re.findall(r'endpoint: "([^"]+)"', source))
    gateway_paths = {
        value
        for name, value in vars(gateway_routes).items()
        if name.isupper() and isinstance(value, str)
    }
    canonical = gateway_paths | {BROWSER_LIVE_PATH}

    assert len(declared) >= 50
    assert declared <= canonical


def test_every_production_devpreview_api_module_has_a_surface_owner() -> None:
    source = MATRIX_PATH.read_text(encoding="utf-8")
    module_section = source.split("export const SURFACE_API_MODULES", 1)[1].split(
        "export const DYNAMIC_API_ROUTE_EXPANSIONS",
        1,
    )[0]
    declared_modules = set(re.findall(r'"([a-z][a-z0-9-]+)"', module_section))
    imported_modules = _production_api_modules()

    assert imported_modules
    assert imported_modules == declared_modules


def _route_skeleton(path: str) -> str:
    path = path.removeprefix("/api").split("?", 1)[0]
    return re.sub(r"\$\{[^}]+\}|\{[^}]+\}", "{}", path)


def test_every_literal_runtime_api_route_has_a_declared_feed_or_expansion() -> None:
    source = MATRIX_PATH.read_text(encoding="utf-8")
    declared = set(re.findall(r'endpoint: "([^"]+)"', source))
    expansion_section = source.split(
        "export const DYNAMIC_API_ROUTE_EXPANSIONS",
        1,
    )[1]
    expansion_templates = {
        _route_skeleton(path)
        for path in re.findall(r'"(/[^"]+\{[^"]+)"', expansion_section)
    }
    module_section = source.split("export const SURFACE_API_MODULES", 1)[1]
    declared_modules = set(re.findall(r'"([a-z][a-z0-9-]+)"', module_section))
    literal_pattern = re.compile(r"""(?P<quote>["'`])(/api/[^"'`]+)(?P=quote)""")
    runtime_routes: set[str] = set()
    for module in declared_modules:
        path = FRONTEND_SRC / "api" / f"{module}.ts"
        assert path.exists(), module
        runtime_routes.update(
            _route_skeleton(match.group(2))
            for match in literal_pattern.finditer(path.read_text(encoding="utf-8"))
        )

    declared_skeletons = {_route_skeleton(path) for path in declared}
    assert runtime_routes
    assert runtime_routes <= declared_skeletons | expansion_templates
