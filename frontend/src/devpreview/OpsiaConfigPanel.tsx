import { useEffect, useState } from "react";
import { AlertTriangle, FileCog, KeyRound } from "lucide-react";

import { listConfigReferences } from "../api/config-references";
import { listInventoryResourcesByType } from "../api/inventory-query";
import type {
  ConfigReferenceCoverage,
  ConfigReferenceItem,
  ConfigReferenceList,
} from "../api/config-references-schemas";
import type { InventoryResource } from "../api/inventory-schemas";
import type { PodHighlightTarget } from "./podHighlight";
import { podsOwnedByWorkloads } from "./podInventoryHighlight";
import { ResourceAuxiliaryRow } from "./ResourceAuxiliaryPanel";
import { HP, MONO, TINT, TYPE, UI } from "./theme";

type ConfigPanelStatus = "loading" | "ready" | "unavailable";

interface OpsiaConfigPanelProps {
  activeCluster: string | null;
  selectedNamespace: string | null;
  onHighlightTarget?: (target: PodHighlightTarget | null) => void;
}

interface OpsiaConfigPanelView {
  status: ConfigPanelStatus;
  data: ConfigReferenceList | null;
  pods: InventoryResource[];
  replicaSets: InventoryResource[];
}

const CONFIG_INVENTORY_QUERY_LIMIT = 1000;

const COVERAGE_REASON_LABELS: Record<string, string> = {
  inventory_snapshot_unavailable: "인벤토리 스냅샷이 아직 없습니다",
  inventory_resource_repository_unavailable: "인벤토리 리소스 저장소를 읽을 수 없습니다",
  invalid_namespace: "네임스페이스 필터가 올바르지 않습니다",
  workload_collection_not_observed: "workload 수집 범위가 확인되지 않았습니다",
  workload_collection_incomplete: "workload 수집 결과가 일부만 반영되었습니다",
  workload_collection_truncated: "workload 수집 결과가 잘렸습니다",
  deployment_projection_limit_reached: "Deployment 조회 한도까지 표시 중입니다",
  config_reference_projection_limit_reached: "구성 참조 결과가 일부 생략되었습니다",
  config_reference_reason_codes_truncated: "일부 사유가 생략되었습니다",
  source_resources_incomplete: "인벤토리 원본 리소스가 일부만 수집되었습니다",
  source_resources_truncated: "인벤토리 원본 리소스가 잘렸습니다",
};

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

function useOpsiaConfigReferences(
  clusterId: string | null,
  namespace: string | null,
): OpsiaConfigPanelView {
  const [view, setView] = useState<OpsiaConfigPanelView & { key: string }>({
    status: "loading",
    data: null,
    pods: [],
    replicaSets: [],
    key: "",
  });
  const cid = clusterId?.trim() ?? "";
  const ns = namespace?.trim() ?? "";
  const key = cid ? `${cid}\u0000${ns}` : "";

  useEffect(() => {
    if (!key) return;
    const [requestedClusterId, requestedNamespace = ""] = key.split("\u0000");
    const controller = new AbortController();
    const optionalInventory = (resourceType: string): Promise<InventoryResource[]> => (
      listInventoryResourcesByType(
        requestedClusterId,
        {
          resourceType,
          namespace: requestedNamespace || null,
          limit: CONFIG_INVENTORY_QUERY_LIMIT,
        },
        controller.signal,
      ).then((response) => response.resources).catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) throw cause;
        return [];
      })
    );
    void Promise.all([
      listConfigReferences(
        requestedClusterId,
        { namespace: requestedNamespace || null },
        controller.signal,
      ),
      optionalInventory("pod"),
      optionalInventory("replicaset"),
    ]).then(([response, pods, replicaSets]) => {
      if (controller.signal.aborted) return;
      setView({
        status: "ready",
        data: response,
        pods,
        replicaSets,
        key,
      });
    }).catch((cause: unknown) => {
      if (controller.signal.aborted || isAbortError(cause)) return;
      setView({
        status: "unavailable",
        data: null,
        pods: [],
        replicaSets: [],
        key,
      });
    });
    return () => controller.abort();
  }, [key]);

  if (!key) return { status: "ready", data: null, pods: [], replicaSets: [] };
  return view.key === key
    ? view
    : { status: "loading", data: null, pods: [], replicaSets: [] };
}

function PanelEmptyState({ label, hint }: { label: string; hint: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8, padding: "26px 14px", textAlign: "center" }}>
      <FileCog size={20} style={{ color: UI.ink3 }} />
      <span style={{ fontSize: TYPE.label, fontWeight: 600, color: UI.ink2 }}>{label}</span>
      <span style={{ fontSize: TYPE.caption, color: UI.ink3, lineHeight: 1.5 }}>{hint}</span>
    </div>
  );
}

function ConfigSkeletonList() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "4px 2px" }}>
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 9px" }}>
          <span className="op-skel" style={{ width: 14, height: 14, borderRadius: 4, flexShrink: 0 }} />
          <span style={{ flex: 1, display: "grid", gap: 5 }}>
            <span className="op-skel" style={{ width: `${72 - i * 7}%`, height: 10, borderRadius: 5 }} />
            <span className="op-skel" style={{ width: "54%", height: 8, borderRadius: 4 }} />
          </span>
        </div>
      ))}
    </div>
  );
}

function CoverageNote({ coverage }: { coverage: ConfigReferenceCoverage }) {
  if (coverage.availability !== "partial" || coverage.reason_codes.length === 0) return null;
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 6, padding: "7px 8px", border: `1px solid ${HP.warn}33`, borderRadius: 8, background: `${HP.warn}0F`, color: UI.ink2 }}>
      <AlertTriangle size={13} style={{ color: HP.warn, flexShrink: 0, marginTop: 1 }} />
      <span style={{ fontSize: TYPE.caption, lineHeight: 1.45 }}>{coverageReasonText(coverage)}</span>
    </div>
  );
}

function referencedWorkloadCount(item: ConfigReferenceItem): number {
  return new Set(
    item.referenced_by.map((usage) => (
      usage.workload.uid ?? `${usage.workload.namespace}/${usage.workload.name}`
    )),
  ).size;
}

function ConfigReferenceRow({
  item,
  clusterId,
  pods,
  replicaSets,
  onHighlightTarget,
}: {
  item: ConfigReferenceItem;
  clusterId: string;
  pods: readonly InventoryResource[];
  replicaSets: readonly InventoryResource[];
  onHighlightTarget?: (target: PodHighlightTarget | null) => void;
}) {
  const Icon = item.kind === "Secret" ? KeyRound : FileCog;
  const iconColor = item.kind === "Secret" ? TINT.purple.fg : TINT.blue.fg;
  const workloadCount = referencedWorkloadCount(item);
  const workloads = [...new Map(
    item.referenced_by.map((usage) => [
      `${usage.workload.kind}\u0000${usage.workload.namespace}\u0000${usage.workload.name}`,
      {
        kind: usage.workload.kind,
        namespace: usage.workload.namespace,
        name: usage.workload.name,
      },
    ]),
  ).values()];
  const matchedPods = podsOwnedByWorkloads(
    clusterId,
    workloads,
    replicaSets,
    pods,
  );
  const highlightTarget: PodHighlightTarget = matchedPods.length > 0
    ? { type: "pods", pods: matchedPods }
    : {
        type: "workloads",
        clusterId,
        workloads,
      };

  return (
    <ResourceAuxiliaryRow
      className="rrow"
      data-pod-highlight-source="config"
      tabIndex={0}
      onMouseEnter={() => onHighlightTarget?.(highlightTarget)}
      onMouseLeave={() => onHighlightTarget?.(null)}
      onFocus={() => onHighlightTarget?.(highlightTarget)}
      onBlur={() => onHighlightTarget?.(null)}
      icon={<Icon size={15} style={{ color: iconColor }} />}
      title={item.name}
      tooltip={item.name}
      titleFontFamily={MONO}
      meta={`${item.namespace} · Deployment ${workloadCount}개 참조`}
      trailing={<span title={`연결된 파드 ${matchedPods.length}개`}>{matchedPods.length}</span>}
    />
  );
}

function emptyHint(namespace: string | null, coverage: ConfigReferenceCoverage | null): string {
  if (coverage?.reason_codes.includes("workload_collection_not_observed")) {
    return namespace
      ? `${namespace} 네임스페이스의 workload 수집 범위가 아직 확인되지 않았습니다.`
      : "workload 수집 범위가 아직 확인되지 않았습니다.";
  }
  return namespace
    ? `${namespace} 네임스페이스에서 Deployment가 참조하는 ConfigMap·Secret이 없습니다.`
    : "선택한 클러스터에서 Deployment가 참조하는 ConfigMap·Secret이 없습니다.";
}

function coverageReasonText(coverage: ConfigReferenceCoverage | null): string {
  const labels = coverage?.reason_codes.map((reason) => COVERAGE_REASON_LABELS[reason] ?? reason) ?? [];
  return labels.length > 0 ? labels.join(" · ") : "인벤토리 응답을 다시 확인하세요.";
}

export function OpsiaConfigPanel({
  activeCluster,
  selectedNamespace,
  onHighlightTarget,
}: OpsiaConfigPanelProps) {
  const namespaceFilter = selectedNamespace?.trim() || null;
  const configView = useOpsiaConfigReferences(activeCluster, namespaceFilter);
  const items = configView.data?.items ?? [];
  const coverage = configView.data?.coverage ?? null;
  const coverageUnavailable = coverage?.availability === "unavailable";

  return (
    <>
      {!activeCluster ? (
        <PanelEmptyState label="클러스터를 선택하세요" hint="구성 참조는 클러스터 범위에서 표시됩니다." />
      ) : configView.status === "loading" ? (
        <ConfigSkeletonList />
      ) : configView.status === "unavailable" ? (
        <PanelEmptyState label="구성 참조를 불러오지 못했습니다" hint="인벤토리 응답을 다시 확인하세요." />
      ) : coverageUnavailable ? (
        <PanelEmptyState label="구성 참조를 확인할 수 없습니다" hint={coverageReasonText(coverage)} />
      ) : items.length === 0 ? (
        <PanelEmptyState label="참조된 ConfigMap·Secret이 없습니다" hint={emptyHint(namespaceFilter, coverage)} />
      ) : (
        <>
          {coverage && <CoverageNote coverage={coverage} />}
          <div style={{ display: "grid", gap: 2, marginTop: coverage ? 8 : 0 }}>
            {items.map((item) => (
              <ConfigReferenceRow
                key={`${item.kind}:${item.namespace}/${item.name}`}
                item={item}
                clusterId={activeCluster}
                pods={configView.pods}
                replicaSets={configView.replicaSets}
                onHighlightTarget={onHighlightTarget}
              />
            ))}
          </div>
        </>
      )}
    </>
  );
}
