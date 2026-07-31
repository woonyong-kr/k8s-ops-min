from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

KUBERNETES_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")


def select_resources(
    documents: Iterable[Any],
    *,
    kind: str,
    names: frozenset[str],
) -> tuple[Mapping[str, Any], ...]:
    if not kind or any(character.isspace() for character in kind):
        raise ValueError("kind must be a non-empty value without whitespace")
    if not names or any(not KUBERNETES_NAME.fullmatch(name) for name in names):
        raise ValueError("names must contain valid Kubernetes resource names")

    selected: dict[str, Mapping[str, Any]] = {}
    for index, document in enumerate(documents):
        if document is None:
            continue
        if not isinstance(document, Mapping):
            raise ValueError(f"manifest document {index} must be a mapping")
        if document.get("kind") != kind:
            continue
        metadata = document.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError(f"manifest document {index} metadata must be a mapping")
        name = metadata.get("name")
        if name not in names:
            continue
        if name in selected:
            raise ValueError(f"manifest contains duplicate {kind}/{name}")
        selected[name] = document

    missing = sorted(names - selected.keys())
    if missing:
        raise ValueError(f"manifest is missing {kind} resource(s): {missing!r}")
    return tuple(selected[name] for name in sorted(names))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select an exact fail-closed Kubernetes resource set from a rendered manifest."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--name", action="append", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    documents = yaml.safe_load_all(args.input.read_text(encoding="utf-8"))
    selected = select_resources(
        documents,
        kind=args.kind,
        names=frozenset(args.name),
    )
    yaml.safe_dump_all(selected, sys.stdout, sort_keys=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
