"""safe-pr-worker — safe_pr.requested -> safe_pr.patch_prepared/safe_pr.failed."""

from __future__ import annotations

from collections.abc import AsyncIterator

from domains.scm.events import SafePrFailedBody, SafePrRequestedBody
from domains.scm.pipeline import normalize_safe_pr_request, patch_prepared_body
from domains.scm.policy import (
    MESSAGE_PROVIDER_MISMATCH,
    REASON_PROVIDER_MISMATCH,
    STAGE_PREPARE,
    DefaultSafePrPreflightPolicy,
    SafePrPolicyResult,
    safe_pr_failed_body,
)
from packages.config.settings import env
from packages.contracts.event_bus.bodies import EventBody
from packages.runtime.app import App

app = App("safe-pr-worker")

SCM_PROVIDER_ENV = "SCM_PROVIDER"
DEFAULT_SCM_PROVIDER = "github"
PREPARE_POLICY = DefaultSafePrPreflightPolicy()


def active_scm_provider() -> str:
    return env(SCM_PROVIDER_ENV, DEFAULT_SCM_PROVIDER).strip().lower() or DEFAULT_SCM_PROVIDER


def provider_mismatch_body(request: SafePrRequestedBody) -> SafePrFailedBody:
    return safe_pr_failed_body(
        request,
        SafePrPolicyResult.reject(
            reason_code=REASON_PROVIDER_MISMATCH,
            message=MESSAGE_PROVIDER_MISMATCH,
            details={"event_provider": request.provider, "worker_provider": active_scm_provider()},
        ),
        stage=STAGE_PREPARE,
    )


@app.on(SafePrRequestedBody)
async def on_safe_pr_requested(evt: SafePrRequestedBody) -> AsyncIterator[EventBody]:
    request = normalize_safe_pr_request(evt)
    if request.provider != active_scm_provider():
        yield provider_mismatch_body(request)
        return
    preflight = PREPARE_POLICY.evaluate(request)
    if not preflight.allowed:
        yield safe_pr_failed_body(request, preflight, stage=STAGE_PREPARE)
        return
    yield patch_prepared_body(request)


if __name__ == "__main__":
    app.run()
