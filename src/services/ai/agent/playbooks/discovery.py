from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable


def load_rule_modules(package_name: str, excluded: Iterable[str]) -> None:
    package = importlib.import_module(package_name)
    package_path = getattr(package, "__path__", None)
    if package_path is None:
        return

    excluded_names = set(excluded)
    for module in pkgutil.iter_modules(package_path):
        if module.ispkg or module.name.startswith("_") or module.name in excluded_names:
            continue
        importlib.import_module(f"{package_name}.{module.name}")
