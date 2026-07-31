import { useEffect, useState } from "react";
import {
  Box,
  Braces,
  CreditCard,
  Database,
  Globe2,
  KeyRound,
  Layers3,
  Network,
  Plug,
  Search,
  ShoppingCart,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { listInventoryResourcesByType } from "../api/inventory-query";
import type { InventoryResource } from "../api/inventory-schemas";
import type {
  PodHighlightIdentity,
  PodHighlightTarget,
} from "./podHighlight";
import { podsSelectedByService } from "./podInventoryHighlight";
import { ResourceAuxiliaryRow } from "./ResourceAuxiliaryPanel";
import { BLUE, HP, IDENT, MONO, TINT, TYPE, UI } from "./theme";
import { statusLabel } from "./statusLabel";

type ServicePanelStatus = "loading" | "ready" | "unavailable";
const SERVICE_RESOURCE_TYPE = "service";
const POD_RESOURCE_TYPE = "pod";

interface OpsiaServiceRow {
  name: string;
  ns: string | undefined;
  status: string;
  _key: string;
  matchedPods: PodHighlightIdentity[];
}

interface OpsiaServicePanelProps {
  activeCluster: string | null;
  selectedNamespace: string | null;
  onHighlightTarget?: (target: PodHighlightTarget | null) => void;
}

interface OpsiaServicePanelView {
  status: ServicePanelStatus;
  rows: OpsiaServiceRow[];
}

function textValue(value: unknown, fallback = "-"): string {
  return typeof value === "string" && value.trim() !== "" ? value : fallback;
}

function PanelEmptyState({ label, hint }: { label: string; hint: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8, padding: "26px 14px", textAlign: "center" }}>
      <Network size={20} style={{ color: UI.ink3 }} />
      <span style={{ fontSize: TYPE.label, fontWeight: 600, color: UI.ink2 }}>{label}</span>
      <span style={{ fontSize: TYPE.caption, color: UI.ink3, lineHeight: 1.5 }}>{hint}</span>
    </div>
  );
}

function ServiceSkeletonList() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "4px 2px" }}>
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 9px" }}>
          <span className="op-skel" style={{ width: 14, height: 14, borderRadius: 4, flexShrink: 0 }} />
          <span style={{ flex: 1, display: "grid", gap: 5 }}>
            <span className="op-skel" style={{ width: `${72 - i * 7}%`, height: 10, borderRadius: 5 }} />
            <span className="op-skel" style={{ width: "46%", height: 8, borderRadius: 4 }} />
          </span>
        </div>
      ))}
    </div>
  );
}

const SERVICE_QUERY_LIMIT = 1000;

interface ServiceIconStyle {
  Icon: LucideIcon;
  color: string;
}

/**
 * Exact service names are intentionally not hard-coded. Stable, generic name
 * hints give familiar roles distinct glyphs while unknown services keep the
 * neutral Kubernetes Service plug.
 */
export function serviceIconStyle(name: string): ServiceIconStyle {
  const normalized = name.toLowerCase();
  if (/(auth|oauth|identity|login|sso)/.test(normalized)) return { Icon: KeyRound, color: TINT.purple.fg };
  if (/(redis|cache|memcached)/.test(normalized)) return { Icon: Layers3, color: IDENT.ruby };
  if (/(gateway|proxy|ingress|router)/.test(normalized)) return { Icon: Network, color: IDENT.indigo };
  if (/(search|elastic|opensearch)/.test(normalized)) return { Icon: Search, color: IDENT.teal };
  if (/(payment|billing|card)/.test(normalized)) return { Icon: CreditCard, color: HP.crit };
  if (/(checkout|cart|order)/.test(normalized)) return { Icon: ShoppingCart, color: HP.warn };
  if (/(web|frontend|ui)(-|$)/.test(normalized)) return { Icon: Globe2, color: HP.ok };
  if (/(api|server|backend)/.test(normalized)) return { Icon: Braces, color: BLUE };
  if (/(worker|consumer|processor)/.test(normalized)) return { Icon: Box, color: IDENT.jade };
  if (/(db|database|postgres|mysql|mongo)/.test(normalized)) return { Icon: Database, color: TINT.purple.fg };
  return { Icon: Plug, color: UI.ink3 };
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

function toServiceRow(
  resource: InventoryResource,
  clusterId: string,
  pods: readonly InventoryResource[],
): OpsiaServiceRow {
  return {
    name: resource.name,
    ns: resource.namespace ?? undefined,
    status: resource.status,
    _key: resource.inventory_key,
    matchedPods: podsSelectedByService(clusterId, resource, pods),
  };
}

function useOpsiaServices(
  clusterId: string | null,
  namespace: string | null,
): OpsiaServicePanelView {
  const [view, setView] = useState<OpsiaServicePanelView & { key: string }>({
    status: "loading",
    rows: [],
    key: "",
  });
  const cid = clusterId?.trim() ?? "";
  const ns = namespace?.trim() ?? "";
  const key = cid ? `${cid}\u0000${ns}` : "";

  useEffect(() => {
    if (!key) return;
    const [requestedClusterId, requestedNamespace = ""] = key.split("\u0000");
    const controller = new AbortController();
    const podResources = listInventoryResourcesByType(
      requestedClusterId,
      {
        resourceType: POD_RESOURCE_TYPE,
        namespace: requestedNamespace || null,
        limit: SERVICE_QUERY_LIMIT,
      },
      controller.signal,
    ).then((response) => response.resources).catch((cause: unknown) => {
      if (controller.signal.aborted || isAbortError(cause)) throw cause;
      return [];
    });
    void Promise.all([
      listInventoryResourcesByType(
        requestedClusterId,
        {
          resourceType: SERVICE_RESOURCE_TYPE,
          namespace: requestedNamespace || null,
          limit: SERVICE_QUERY_LIMIT,
        },
        controller.signal,
      ),
      podResources,
    ]).then(([response, pods]) => {
      if (controller.signal.aborted) return;
      const observedPods = pods.filter(
        (resource) => resource.kind.toLowerCase() === POD_RESOURCE_TYPE,
      );
      const rows = response.resources
        .filter((resource) => resource.kind.toLowerCase() === SERVICE_RESOURCE_TYPE)
        .map((resource) => toServiceRow(resource, requestedClusterId, observedPods));
      setView({ status: "ready", rows, key });
    }).catch((cause: unknown) => {
      if (controller.signal.aborted || isAbortError(cause)) return;
      setView({ status: "unavailable", rows: [], key });
    });
    return () => controller.abort();
  }, [key]);

  if (!key) return { status: "ready", rows: [] };
  return view.key === key ? view : { status: "loading", rows: [] };
}

export function OpsiaServicePanel({
  activeCluster,
  selectedNamespace,
  onHighlightTarget,
}: OpsiaServicePanelProps) {
  const namespaceFilter = selectedNamespace?.trim() || null;
  const serviceView = useOpsiaServices(activeCluster, namespaceFilter);

  return (
    <>
      {!activeCluster ? (
        <PanelEmptyState label="클러스터를 선택하세요" hint="서비스 목록은 클러스터 범위에서 표시됩니다." />
      ) : serviceView.status === "loading" ? (
        <ServiceSkeletonList />
      ) : serviceView.status === "unavailable" ? (
        <PanelEmptyState label="서비스를 불러오지 못했습니다" hint="인벤토리 응답을 다시 확인하세요." />
      ) : serviceView.rows.length === 0 ? (
        <PanelEmptyState
          label="관측된 서비스가 없습니다"
          hint={namespaceFilter ? `${namespaceFilter} 네임스페이스에서 관측된 Service가 없습니다.` : "선택한 클러스터에서 관측된 Service가 없습니다."}
        />
      ) : (
        <div style={{ display: "grid", gap: 2 }}>
        {serviceView.rows.map((service) => {
          const name = textValue(service.name, "이름 없음");
          const { Icon, color } = serviceIconStyle(name);
          const namespace = textValue(service.ns, "클러스터 범위");
          const relationNamespace = typeof service.ns === "string" ? service.ns : "";
          const status = textValue(service.status);
          const highlightTarget: PodHighlightTarget | null = activeCluster
            ? service.matchedPods.length > 0
              ? {
                  type: "pods",
                  pods: service.matchedPods,
                }
              : {
                type: "service",
                clusterId: activeCluster,
                namespace: relationNamespace,
                name,
              }
            : null;
          return (
            <ResourceAuxiliaryRow
              key={textValue(service._key, `${namespace}/${name}`)}
              className="rrow"
              data-pod-highlight-source="service"
              tabIndex={0}
              onMouseEnter={() => onHighlightTarget?.(highlightTarget)}
              onMouseLeave={() => onHighlightTarget?.(null)}
              onFocus={() => onHighlightTarget?.(highlightTarget)}
              onBlur={() => onHighlightTarget?.(null)}
              icon={<Icon size={15} style={{ color }} />}
              title={name}
              tooltip={name}
              titleFontFamily={MONO}
              meta={`${namespace} · ${statusLabel(status)}`}
              trailing={<span title={`연결된 파드 ${service.matchedPods.length}개`}>{service.matchedPods.length}</span>}
            />
          );
        })}
        </div>
      )}
    </>
  );
}
