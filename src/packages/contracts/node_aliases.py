"""Contracts for user-scoped Kubernetes node display aliases."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.modeling import StrictModel

NODE_ALIAS_MAX_LENGTH = 80


class NodeAliasItem(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=gateway_limits.CLUSTER_ID_MAX_LENGTH)
    node_name: str = Field(min_length=1, max_length=gateway_limits.KUBERNETES_NAME_MAX_LENGTH)
    alias: str = Field(min_length=1, max_length=NODE_ALIAS_MAX_LENGTH)
    revision: int = Field(ge=1)
    updated_at: str | None = None


class NodeAliasListResponse(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=gateway_limits.CLUSTER_ID_MAX_LENGTH)
    aliases: tuple[NodeAliasItem, ...] = ()


class NodeAliasUpdateRequest(StrictModel):
    alias: str = Field(min_length=1, max_length=NODE_ALIAS_MAX_LENGTH)

    @field_validator("alias", mode="before")
    @classmethod
    def normalize_alias(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("node alias is required")
        return normalized
