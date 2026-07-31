"""Fail-closed mutation policy shared by the demo seed and reset boundaries."""

from __future__ import annotations

import os
from collections.abc import Mapping

DEMO_WORKSPACE_MUTATIONS_ENV = "OPSIA_DEMO_WORKSPACE_MUTATIONS"
DEMO_WORKSPACE_MUTATIONS_OPT_IN = "demo-workspace-v1"


def require_demo_workspace_mutation_opt_in(
    environ: Mapping[str, str] | None = None,
) -> None:
    values = os.environ if environ is None else environ
    if values.get(DEMO_WORKSPACE_MUTATIONS_ENV, "").strip() != DEMO_WORKSPACE_MUTATIONS_OPT_IN:
        raise RuntimeError(
            f"{DEMO_WORKSPACE_MUTATIONS_ENV} must equal {DEMO_WORKSPACE_MUTATIONS_OPT_IN!r}"
        )
