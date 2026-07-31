"""git-pull-worker — 깃 변경 감지(풀링) → git.changed. 파이프라인 입구.

우현 원본 GitOpsSyncWorkflow.handle()의 commit_sha 결정과 GIT_CHANGED
발행 블록에 대응. 변경 감지 책임만 떼어 이후 렌더/비교/명령 단계가
독립적으로 재시도·테스트될 수 있게 분리.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from domains.gitops.events import GitChangedBody, GitWebhookReceivedBody
from domains.gitops.repository import (
    derive_application_id,
    derive_deployment_binding_id,
    derive_repository_id,
    derive_watch_target_id,
    derive_workflow_run_id,
)
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.event_bus.interfaces import JsonObject
from packages.runtime.app import App, EventContext

app = App("git-pull-worker")


def normalize_gitops_identity(payload: JsonObject) -> JsonObject:
    repository_id = derive_repository_id(payload)
    watch_target_id = derive_watch_target_id({**payload, "repository_id": repository_id})
    binding_id = derive_deployment_binding_id(
        {**payload, "repository_id": repository_id, "watch_target_id": watch_target_id}
    )
    scoped = {
        **payload,
        "repository_id": repository_id,
        "watch_target_id": watch_target_id,
        "binding_id": binding_id,
    }
    application_id = derive_application_id(scoped)
    return {
        **scoped,
        "application_id": application_id,
        "workflow_run_id": derive_workflow_run_id({**scoped, "application_id": application_id}),
    }


@app.on(GitWebhookReceivedBody)
async def on_git_webhook(
    evt: GitWebhookReceivedBody, ctx: EventContext
) -> AsyncIterator[EventBody]:
    # Git webhook은 계약 객체로 정규화되어 들어오므로 commit 식별자만 보정함.
    # manifest 생성은 다음 단계 manifest-render-worker 책임임.
    commit_sha = evt.commit_sha or str(uuid.uuid4())[:8]
    identity = normalize_gitops_identity(evt.to_body())
    last_seen = await ctx.db.get_watch_last_seen_commit_sha(
        str(identity["watch_target_id"]), str(identity["workspace_id"])
    )
    if last_seen == commit_sha and not evt.force:
        return
    yield GitChangedBody(
        commit_sha=commit_sha,
        image=evt.image,
        replicas=evt.replicas,
        workspace_id=str(identity["workspace_id"]),
        repository_id=str(identity["repository_id"]),
        repo_ref=evt.repo_ref,
        branch=evt.branch,
        watch_target_id=str(identity["watch_target_id"]),
        binding_id=str(identity["binding_id"]),
        application_id=str(identity["application_id"]),
        workflow_run_id=str(identity["workflow_run_id"]),
        environment=evt.environment,
        cluster_id=evt.cluster_id,
        manifest_path=evt.manifest_path,
        source_type=evt.source_type,
    )


if __name__ == "__main__":
    app.run()
