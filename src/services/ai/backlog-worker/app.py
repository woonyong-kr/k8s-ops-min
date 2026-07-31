"""backlog-worker — rca.backlog.created -> RCA backlog read model."""

from __future__ import annotations

from domains.rca.events import RcaBacklogItemCreatedBody
from packages.contracts.stores import RcaBacklogStore
from packages.runtime.app import App, EventContext

app = App("backlog-worker")


@app.on(RcaBacklogItemCreatedBody)
async def on_rca_backlog_item_created(
    evt: RcaBacklogItemCreatedBody,
    ctx: EventContext[RcaBacklogStore],
) -> None:
    await ctx.db.upsert_rca_backlog_item(evt.to_body())


if __name__ == "__main__":
    app.run()
