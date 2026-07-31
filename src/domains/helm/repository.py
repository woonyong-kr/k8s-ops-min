"""Database reads for Helm release storage metadata only."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.helm.models import HelmChartSourceRecord
from domains.helm.source_provider import (
    helm_chart_credential_provider,
    helm_chart_credential_scope,
    helm_chart_source_from_row,
    helm_chart_source_id,
    normalize_helm_chart_source_reference,
)
from domains.inventory.models import ClusterInventoryResourceRecord
from domains.inventory_filter.models import InventoryFilterRevision
from packages.contracts.helm.sources import (
    HELM_CHART_SOURCE_PAGE_MAX,
    HelmChartSource,
    HelmChartSourcePage,
)
from packages.runtime.keyset_cursor import decode_keyset_cursor, encode_keyset_cursor
from packages.security.credentials import CredentialEncryptionError, parse_credential_ref
from packages.storage.engine import DatabaseConnection, iso_or_none

HELM_STORAGE_OWNER_LABEL = "owner"
HELM_STORAGE_OWNER_VALUE = "helm"
HELM_STORAGE_KINDS = ("secret", "configmap")
HELM_MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
HELM_MANAGED_BY_VALUE = "Helm"
HELM_RELEASE_NAME_ANNOTATION = "meta.helm.sh/release-name"
HELM_RELEASE_NAMESPACE_ANNOTATION = "meta.helm.sh/release-namespace"
HELM_CHART_LABEL = "helm.sh/chart"


class HelmChartSourceConflict(RuntimeError):
    """A workspace already has the same source identity or display name."""


class HelmChartSourceNotFound(RuntimeError):
    """No source exists for the exact workspace and source ID."""


class HelmChartSourceIdentityConflict(RuntimeError):
    """The listed optimistic source identity no longer matches storage."""


@dataclass(frozen=True)
class HelmOwnedResourceObservationBatch:
    """Bounded safe metadata rows used for release ownership correlation."""

    rows: tuple[dict[str, Any], ...]
    truncated: bool


@dataclass(frozen=True)
class HelmChartSourceRecordBatch:
    """Bounded internal source rows used only after workspace RBAC filtering."""

    rows: tuple[dict[str, Any], ...]
    truncated: bool


class HelmReleaseRepository(DatabaseConnection):
    """Read current Helm storage labels without reading Secret data."""

    def register_helm_chart_source(
        self,
        *,
        workspace_id: str,
        provider: str,
        name: str,
        reference: str,
        credential_ref: str | None = None,
        access_policy: dict[str, Any] | None = None,
    ) -> HelmChartSource:
        """Atomically register one canonical source without persisting raw credentials."""

        normalized_workspace = workspace_id.strip()
        normalized_name = name.strip()
        if not normalized_workspace or not normalized_name:
            raise ValueError("workspace_id and source name are required")
        canonical_ref = normalize_helm_chart_source_reference(provider, reference)
        source_id = helm_chart_source_id(
            normalized_workspace,
            provider,
            canonical_ref,
        )
        if credential_ref is not None:
            try:
                credential_provider, credential_scope = parse_credential_ref(credential_ref)
            except CredentialEncryptionError as exc:
                raise ValueError("invalid Helm chart source credential reference") from exc
            if credential_provider != helm_chart_credential_provider(
                provider
            ) or credential_scope != helm_chart_credential_scope(source_id):
                raise ValueError("invalid Helm chart source credential reference")
        table = HelmChartSourceRecord.__table__
        statement = (
            pg_insert(table)
            .values(
                source_id=source_id,
                workspace_id=normalized_workspace,
                provider=provider,
                name=normalized_name,
                canonical_ref=canonical_ref,
                credential_ref=credential_ref,
                status="active",
                access_policy=dict(access_policy or {}),
                updated_at=func.now(),
            )
            .on_conflict_do_nothing()
            .returning(table)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().one_or_none()
        if row is None:
            raise HelmChartSourceConflict("Helm chart source already exists")
        return helm_chart_source_from_row(dict(row))

    def list_helm_chart_sources(
        self,
        *,
        workspace_id: str,
        limit: int,
        cursor: str | None = None,
        source_ids: Collection[str] | None = None,
    ) -> HelmChartSourcePage:
        """List one workspace's safe source projections with bounded keyset pagination."""

        effective_limit = min(max(int(limit), 1), HELM_CHART_SOURCE_PAGE_MAX)
        if not workspace_id:
            return HelmChartSourcePage(
                items=(),
                limit=effective_limit,
                has_more=False,
                next_cursor=None,
            )
        table = HelmChartSourceRecord.__table__
        scope = f"helm-chart-sources:{workspace_id}"
        statement = select(
            table.c.source_id,
            table.c.workspace_id,
            table.c.provider,
            table.c.name,
            table.c.canonical_ref,
            table.c.credential_ref,
            table.c.status,
            table.c.updated_at,
        ).where(table.c.workspace_id == workspace_id)
        if source_ids is not None:
            allowed_source_ids = _ids(source_ids)
            if not allowed_source_ids:
                return HelmChartSourcePage(
                    items=(),
                    limit=effective_limit,
                    has_more=False,
                    next_cursor=None,
                )
            statement = statement.where(table.c.source_id.in_(allowed_source_ids))
        if cursor is not None:
            position = decode_keyset_cursor(cursor, expected_scope=scope)
            statement = statement.where(
                or_(
                    table.c.updated_at < position.ordered_at,
                    and_(
                        table.c.updated_at == position.ordered_at,
                        table.c.source_id > position.tie_breaker,
                    ),
                )
            )
        statement = statement.order_by(table.c.updated_at.desc(), table.c.source_id).limit(
            effective_limit + 1
        )
        with self.connection() as conn:
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
        has_more = len(rows) > effective_limit
        page = rows[:effective_limit]
        next_cursor = None
        if has_more and page:
            next_cursor = encode_keyset_cursor(
                scope=scope,
                ordered_at=page[-1]["updated_at"],
                tie_breaker=str(page[-1]["source_id"]),
            )
        return HelmChartSourcePage(
            items=tuple(helm_chart_source_from_row(row) for row in page),
            limit=effective_limit,
            has_more=has_more,
            next_cursor=next_cursor,
        )

    def get_helm_chart_source_record(
        self,
        *,
        workspace_id: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        """Return one internal provider record, scoped before credential access."""

        if not workspace_id or not source_id:
            return None
        table = HelmChartSourceRecord.__table__
        statement = (
            select(
                table.c.source_id,
                table.c.workspace_id,
                table.c.provider,
                table.c.name,
                table.c.canonical_ref,
                table.c.credential_ref,
                table.c.status,
                table.c.access_policy,
                table.c.updated_at,
            )
            .where(
                table.c.workspace_id == workspace_id,
                table.c.source_id == source_id,
            )
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def list_helm_chart_source_records(
        self,
        *,
        workspace_id: str,
        source_ids: Collection[str] | None,
        limit: int,
    ) -> HelmChartSourceRecordBatch:
        """Read active authorized provider records with an explicit hard bound."""

        effective_limit = max(1, int(limit))
        if not workspace_id:
            return HelmChartSourceRecordBatch(rows=(), truncated=False)
        table = HelmChartSourceRecord.__table__
        statement = select(
            table.c.source_id,
            table.c.workspace_id,
            table.c.provider,
            table.c.name,
            table.c.canonical_ref,
            table.c.credential_ref,
            table.c.status,
            table.c.updated_at,
        ).where(
            table.c.workspace_id == workspace_id,
            table.c.status == "active",
        )
        if source_ids is not None:
            allowed_source_ids = _ids(source_ids)
            if not allowed_source_ids:
                return HelmChartSourceRecordBatch(rows=(), truncated=False)
            statement = statement.where(table.c.source_id.in_(allowed_source_ids))
        statement = statement.order_by(table.c.source_id).limit(effective_limit + 1)
        with self.connection() as conn:
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
        return HelmChartSourceRecordBatch(
            rows=tuple(rows[:effective_limit]),
            truncated=len(rows) > effective_limit,
        )

    def delete_helm_chart_source(
        self,
        *,
        workspace_id: str,
        source_id: str,
        expected_provider: str,
        expected_name: str,
        expected_reference: str,
    ) -> dict[str, Any]:
        """Lock and delete one source only when its listed identity still matches."""

        normalized_workspace = workspace_id.strip()
        normalized_source_id = source_id.strip()
        normalized_name = expected_name.strip()
        if not normalized_workspace or not normalized_source_id or not normalized_name:
            raise ValueError("workspace and Helm chart source identity are required")
        canonical_ref = normalize_helm_chart_source_reference(
            expected_provider,
            expected_reference,
        )
        table = HelmChartSourceRecord.__table__
        locked = (
            select(
                table.c.source_id,
                table.c.workspace_id,
                table.c.provider,
                table.c.name,
                table.c.canonical_ref,
                table.c.credential_ref,
            )
            .where(
                table.c.workspace_id == normalized_workspace,
                table.c.source_id == normalized_source_id,
            )
            .with_for_update()
        )
        with self.connection() as conn:
            row = conn.execute(locked).mappings().first()
            if row is None:
                raise HelmChartSourceNotFound
            current = dict(row)
            if (
                str(current.get("provider") or "") != expected_provider
                or str(current.get("name") or "") != normalized_name
                or str(current.get("canonical_ref") or "") != canonical_ref
            ):
                raise HelmChartSourceIdentityConflict
            conn.execute(
                table.delete().where(
                    table.c.workspace_id == normalized_workspace,
                    table.c.source_id == normalized_source_id,
                )
            )
        return current

    def list_helm_storage_observations(
        self,
        *,
        workspace_id: str,
        cluster_ids: Collection[str],
        namespaces: Collection[str],
    ) -> list[dict[str, Any]]:
        clusters = _ids(cluster_ids)
        namespace_values = _ids(namespaces)
        if not workspace_id or not clusters:
            return []
        table = ClusterInventoryResourceRecord.__table__
        statement = select(
            table.c.workspace_id,
            table.c.cluster_id,
            table.c.inventory_key,
            table.c.api_version,
            table.c.kind,
            table.c.namespace,
            table.c.name,
            table.c.uid,
            table.c.resource_version,
            table.c.labels,
            table.c.observed_at,
        ).where(
            table.c.workspace_id == workspace_id,
            table.c.cluster_id.in_(clusters),
            table.c.deleted_at.is_(None),
            func.lower(table.c.kind).in_(HELM_STORAGE_KINDS),
            table.c.labels[HELM_STORAGE_OWNER_LABEL].as_string() == HELM_STORAGE_OWNER_VALUE,
        )
        if namespace_values:
            statement = statement.where(table.c.namespace.in_(namespace_values))
        statement = statement.order_by(
            table.c.cluster_id,
            table.c.namespace,
            table.c.name,
            table.c.inventory_key,
        )
        with self.connection() as conn:
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
        return [_serialize_storage_row(row) for row in rows]

    def list_helm_owned_resource_observations(
        self,
        *,
        workspace_id: str,
        release_scopes: Collection[tuple[str, str, str]],
        limit: int,
    ) -> HelmOwnedResourceObservationBatch:
        """Read only exact Helm ownership keys and resource presentation metadata."""

        scopes = _release_scopes(release_scopes)
        effective_limit = max(1, limit)
        if not workspace_id or not scopes:
            return HelmOwnedResourceObservationBatch(rows=(), truncated=False)
        table = ClusterInventoryResourceRecord.__table__
        release_name = table.c.annotations[HELM_RELEASE_NAME_ANNOTATION].as_string()
        release_namespace = table.c.annotations[HELM_RELEASE_NAMESPACE_ANNOTATION].as_string()
        managed_by = table.c.labels[HELM_MANAGED_BY_LABEL].as_string()
        chart_label = table.c.labels[HELM_CHART_LABEL].as_string()
        statement = (
            select(
                table.c.workspace_id,
                table.c.cluster_id,
                table.c.inventory_key,
                table.c.api_version,
                table.c.kind,
                table.c.namespace,
                table.c.name,
                table.c.uid,
                table.c.status,
                table.c.health,
                table.c.observed_at,
                release_name.label("release_name"),
                release_namespace.label("release_namespace"),
                chart_label.label("chart_label"),
            )
            .where(
                table.c.workspace_id == workspace_id,
                table.c.deleted_at.is_(None),
                managed_by == HELM_MANAGED_BY_VALUE,
                release_namespace == table.c.namespace,
                tuple_(table.c.cluster_id, table.c.namespace, release_name).in_(scopes),
            )
            .order_by(
                table.c.cluster_id,
                table.c.namespace,
                release_name,
                table.c.kind,
                table.c.name,
                table.c.inventory_key,
            )
            .limit(effective_limit + 1)
        )
        with self.connection() as conn:
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
        truncated = len(rows) > effective_limit
        return HelmOwnedResourceObservationBatch(
            rows=tuple(_serialize_owned_resource_row(row) for row in rows[:effective_limit]),
            truncated=truncated,
        )

    def helm_release_observation_contexts(
        self,
        *,
        workspace_id: str,
        cluster_ids: Collection[str],
    ) -> dict[str, dict[str, Any]]:
        """Return one latest inventory completeness cut per requested cluster."""

        clusters = _ids(cluster_ids)
        if not workspace_id or not clusters:
            return {}
        table = InventoryFilterRevision.__table__
        ranked = (
            select(
                table.c.cluster_id,
                table.c.revision_id,
                table.c.observed_at,
                table.c.labels_complete,
                table.c.resources_complete,
                table.c.partial_reason_codes,
                func.row_number()
                .over(partition_by=table.c.cluster_id, order_by=table.c.revision_id.desc())
                .label("rank"),
            )
            .where(table.c.workspace_id == workspace_id, table.c.cluster_id.in_(clusters))
            .cte("helm_observation_contexts")
        )
        statement = select(ranked).where(ranked.c.rank == 1)
        with self.connection() as conn:
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
        return {
            str(row["cluster_id"]): {
                "snapshot_revision": int(row["revision_id"]),
                "observed_at": iso_or_none(row.get("observed_at")),
                "labels_complete": bool(row["labels_complete"]),
                "resources_complete": bool(row["resources_complete"]),
                "partial_reason_codes": [
                    str(reason) for reason in list(row.get("partial_reason_codes") or [])
                ],
            }
            for row in rows
        }


def _ids(values: Collection[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def _release_scopes(
    values: Collection[tuple[str, str, str]],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            {
                (cluster.strip(), namespace.strip(), release.strip())
                for cluster, namespace, release in values
                if cluster.strip() and namespace.strip() and release.strip()
            }
        )
    )


def _serialize_storage_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace_id": str(row["workspace_id"]),
        "cluster_id": str(row["cluster_id"]),
        "inventory_key": str(row["inventory_key"]),
        "api_version": str(row.get("api_version") or ""),
        "kind": str(row.get("kind") or ""),
        "namespace": str(row.get("namespace") or ""),
        "name": str(row.get("name") or ""),
        "uid": str(row.get("uid") or "") or None,
        "resource_version": str(row.get("resource_version") or "") or None,
        "labels": dict(row.get("labels") or {}),
        "observed_at": iso_or_none(row.get("observed_at")),
    }


def _serialize_owned_resource_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace_id": str(row["workspace_id"]),
        "cluster_id": str(row["cluster_id"]),
        "inventory_key": str(row["inventory_key"]),
        "api_version": str(row.get("api_version") or ""),
        "kind": str(row.get("kind") or ""),
        "namespace": str(row.get("namespace") or ""),
        "name": str(row.get("name") or ""),
        "uid": str(row.get("uid") or "") or None,
        "status": str(row.get("status") or "unknown"),
        "health": str(row.get("health") or "unknown"),
        "observed_at": iso_or_none(row.get("observed_at")),
        "release_name": str(row.get("release_name") or ""),
        "release_namespace": str(row.get("release_namespace") or ""),
        "chart_label": str(row.get("chart_label") or "") or None,
    }
