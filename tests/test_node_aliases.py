from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from domains.shell_state import node_alias_router
from packages.contracts.gateway.responses import NodeSummaryItem
from packages.contracts.node_aliases import NodeAliasUpdateRequest


class FakeNodeAliasDb:
    def __init__(self, *, node_exists: bool = True) -> None:
        self.node_exists = node_exists
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_node_aliases(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("list_node_aliases", kwargs))
        return [
            {
                "cluster_id": kwargs["cluster_id"],
                "node_name": "node-a",
                "alias": "worker-a",
                "revision": 1,
                "updated_at": None,
            }
        ]

    def get_inventory_resource(self, **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append(("get_inventory_resource", kwargs))
        if not self.node_exists:
            return None
        return {
            "name": kwargs["name"],
            "resource_type": node_alias_router.OBSERVED_NODE_RESOURCE_TYPE,
            "kind": node_alias_router.OBSERVED_NODE_KIND,
        }

    def put_node_alias(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_node_alias", kwargs))
        return {
            "cluster_id": kwargs["cluster_id"],
            "node_name": kwargs["node_name"],
            "alias": kwargs["alias"],
            "revision": 1,
            "updated_at": None,
        }

    def delete_node_alias(self, **kwargs: Any) -> bool:
        self.calls.append(("delete_node_alias", kwargs))
        return True


def test_node_alias_request_trims_internal_whitespace() -> None:
    request = NodeAliasUpdateRequest(alias="  worker   a  ")

    assert request.alias == "worker a"


def test_node_alias_request_validates_length_after_normalization() -> None:
    request = NodeAliasUpdateRequest(alias=f"  {'worker-a':<90}  ")

    assert request.alias == "worker-a"


def test_node_summary_contract_is_not_extended_with_display_aliases() -> None:
    assert "alias" not in NodeSummaryItem.model_fields
    assert "display_name" not in NodeSummaryItem.model_fields


def test_list_node_aliases_reads_user_scoped_shell_state(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeNodeAliasDb()
    monkeypatch.setattr(node_alias_router, "require_cluster_access", lambda *args: None)

    response = asyncio.run(
        node_alias_router.list_node_aliases(
            cluster_id="cluster-a",
            current=_current(),
            db=db,
        )
    )

    assert response.aliases[0].node_name == "node-a"
    assert response.aliases[0].alias == "worker-a"
    assert db.calls == [
        (
            "list_node_aliases",
            {
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "cluster_id": "cluster-a",
            },
        )
    ]


def test_put_node_alias_validates_node_identity_before_shell_state_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeNodeAliasDb()
    monkeypatch.setattr(node_alias_router, "require_cluster_access", lambda *args: None)

    response = asyncio.run(
        node_alias_router.put_node_alias(
            payload=NodeAliasUpdateRequest(alias="worker-a"),
            cluster_id="cluster-a",
            node_name="node-a",
            current=_current(),
            db=db,
        )
    )

    assert response.alias == "worker-a"
    assert [name for name, _kwargs in db.calls] == ["get_inventory_resource", "put_node_alias"]
    assert db.calls[0][1]["resource_type"] == node_alias_router.OBSERVED_NODE_RESOURCE_TYPE
    assert db.calls[0][1]["kind"] == node_alias_router.OBSERVED_NODE_KIND


def test_put_node_alias_rejects_unobserved_node(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeNodeAliasDb(node_exists=False)
    monkeypatch.setattr(node_alias_router, "require_cluster_access", lambda *args: None)

    with pytest.raises(node_alias_router.HTTPException) as error:
        asyncio.run(
            node_alias_router.put_node_alias(
                payload=NodeAliasUpdateRequest(alias="worker-a"),
                cluster_id="cluster-a",
                node_name="missing-node",
                current=_current(),
                db=db,
            )
        )

    assert error.value.status_code == 404
    assert [name for name, _kwargs in db.calls] == ["get_inventory_resource"]


def test_delete_node_alias_only_touches_alias_store(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeNodeAliasDb(node_exists=False)
    monkeypatch.setattr(node_alias_router, "require_cluster_access", lambda *args: None)

    asyncio.run(
        node_alias_router.delete_node_alias(
            cluster_id="cluster-a",
            node_name="node-a",
            current=_current(),
            db=db,
        )
    )

    assert db.calls == [
        (
            "delete_node_alias",
            {
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "cluster_id": "cluster-a",
                "node_name": "node-a",
            },
        )
    ]


def _current() -> SimpleNamespace:
    return SimpleNamespace(workspace_id="workspace-a", user_id="user-a", roles=("member",))
