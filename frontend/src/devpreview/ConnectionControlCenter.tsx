import { Check, Plug, RefreshCw, Server, X } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useMemo, useState } from "react";

import type { DisconnectPhase } from "../pages/clusters/ClusterDisconnectDialog";
import { GithubIcon } from "./brandIcons";
import { ClusterLifecycleControl } from "./ClusterLifecycleControl";
import type { DevpreviewCluster } from "./contracts";
import { ProgressNodeRail, type ProgressNode } from "./ProgressNodeRail";
import { RepositoryStatusList } from "./RepositoryStatusList";
import { BLUE, DUR, HP, RADIUS, SPACE, TINT, TYPE, UI, inkA } from "./theme";

interface ConnectionControlCenterProps {
  clusters: readonly DevpreviewCluster[];
  onClose: () => void;
  onConnectCluster: () => void;
  onConnectRepository: () => void;
  onDisconnected: (clusterId: string) => void;
  onLifecyclePhase: (clusterId: string, phase: DisconnectPhase) => void;
  onRefresh: () => void;
  onResumeCluster: (cluster: DevpreviewCluster) => void;
  roles: readonly string[];
}

const ACTIVE_CONNECTION_STAGES = new Set(["ready", "snapshot_received", "agent_connected"]);

export function canResumeClusterConnection(cluster: DevpreviewCluster): boolean {
  if (cluster.role !== "target" || cluster.readOnly) return false;
  const stage = (cluster.connectionStage ?? "").trim().toLowerCase();
  const status = cluster.connectionStatus.trim().toLowerCase();
  return !ACTIVE_CONNECTION_STAGES.has(stage)
    && status !== "online"
    && status !== "connected";
}

export function clusterConnectionProgress(cluster: DevpreviewCluster): ProgressNode[] {
  const stage = (cluster.connectionStage ?? "").trim().toLowerCase();
  const failed = stage === "error" || stage === "expired";
  const agentConnected = ["agent_connected", "snapshot_received", "ready"].includes(stage);
  const snapshotReceived = ["snapshot_received", "ready"].includes(stage);
  const ready = stage === "ready";
  const actionLabel = canResumeClusterConnection(cluster) ? "설치 재개" : null;

  return [
    {
      id: `${cluster.id}-registered`,
      label: "등록",
      state: "complete",
      statusLabel: "서버 등록됨",
    },
    {
      id: `${cluster.id}-agent`,
      label: "에이전트",
      state: agentConnected ? "complete" : failed ? "failed" : "active",
      statusLabel: agentConnected ? "연결됨" : failed ? (stage === "expired" ? "설치 만료" : "연결 오류") : "설치 대기",
      description: failed ? "기존 등록에 새 설치 명령을 발급할 수 있습니다." : "클러스터에서 설치 명령 실행을 기다립니다.",
      activity: failed ? undefined : "waiting",
      tone: failed ? "info" : "warning",
      actionLabel,
    },
    {
      id: `${cluster.id}-snapshot`,
      label: "인벤토리",
      state: snapshotReceived ? "complete" : agentConnected ? "active" : "pending",
      statusLabel: snapshotReceived ? "수신됨" : agentConnected ? "수집 중" : "대기",
      activity: agentConnected && !snapshotReceived ? "running" : undefined,
    },
    {
      id: `${cluster.id}-ready`,
      label: "사용 준비",
      state: ready ? "complete" : snapshotReceived ? "active" : "pending",
      statusLabel: ready ? "준비됨" : snapshotReceived ? "확인 중" : "대기",
      activity: snapshotReceived && !ready ? "running" : undefined,
    },
  ];
}

function stageLabel(cluster: DevpreviewCluster): string {
  const stage = (cluster.connectionStage ?? "").trim().toLowerCase();
  return {
    token_issued: "설치 명령 발급됨",
    awaiting_install: "에이전트 설치 대기",
    agent_connected: "에이전트 연결됨",
    snapshot_received: "첫 인벤토리 수신됨",
    ready: "사용 준비됨",
    expired: "설치 명령 만료",
    error: "연결 오류",
  }[stage] ?? "상태 미관측";
}

function stageTone(cluster: DevpreviewCluster): { fg: string; bg: string; border: string } {
  const stage = (cluster.connectionStage ?? "").trim().toLowerCase();
  if (stage === "ready") return { fg: TINT.ok.fg, bg: TINT.ok.bg, border: TINT.ok.bd };
  if (stage === "error" || stage === "expired") return { fg: TINT.crit.fg, bg: TINT.crit.bg, border: TINT.crit.bd };
  return { fg: TINT.warn.fg, bg: TINT.warn.bg, border: TINT.warn.bd };
}

export function ConnectionControlCenter({
  clusters,
  onClose,
  onConnectCluster,
  onConnectRepository,
  onDisconnected,
  onLifecyclePhase,
  onRefresh,
  onResumeCluster,
  roles,
}: ConnectionControlCenterProps) {
  const reduceMotion = useReducedMotion();
  const targetClusters = useMemo(
    () => clusters.filter((cluster) => cluster.role === "target"),
    [clusters],
  );
  const preferredCluster = targetClusters.find(canResumeClusterConnection) ?? targetClusters[0] ?? null;
  const [selectedClusterId, setSelectedClusterId] = useState<string | null>(preferredCluster?.id ?? null);
  const selectedCluster = targetClusters.find((cluster) => cluster.id === selectedClusterId)
    ?? preferredCluster;

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleEscape);
    };
  }, [onClose]);

  return (
    <motion.div
      aria-label="연결 관리"
      aria-modal="true"
      role="dialog"
      initial={reduceMotion ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: reduceMotion ? 0 : DUR.fade }}
      style={{ position: "fixed", inset: 0, zIndex: 78, display: "grid", placeItems: "center", padding: 24 }}
    >
      <button
        aria-label="연결 관리 닫기"
        onClick={onClose}
        style={{ position: "absolute", inset: 0, border: 0, background: inkA(0.28), backdropFilter: "blur(5px)" }}
        type="button"
      />
      <motion.section
        initial={reduceMotion ? false : { opacity: 0, y: 18, scale: 0.985 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 12, scale: 0.99 }}
        transition={reduceMotion ? { duration: 0 } : { type: "spring", visualDuration: 0.38, bounce: 0.14 }}
        style={{
          position: "relative",
          width: "min(1040px, 100%)",
          maxHeight: "min(760px, calc(100vh - 96px))",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          border: `1px solid ${UI.line}`,
          borderRadius: 24,
          background: UI.bg,
          boxShadow: `0 34px 90px -28px ${inkA(0.46)}`,
        }}
      >
        <header style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: 13, padding: "20px 22px", borderBottom: `1px solid ${UI.line}`, background: UI.card }}>
          <span style={{ width: 40, height: 40, display: "grid", placeItems: "center", borderRadius: 13, color: UI.card, background: `linear-gradient(135deg, ${BLUE}, #55b8ff)` }}>
            <Plug size={19} />
          </span>
          <span style={{ minWidth: 0, flex: 1 }}>
            <strong style={{ display: "block", color: UI.ink, fontSize: TYPE.section }}>연결 관리</strong>
            <span style={{ display: "block", marginTop: 3, color: UI.ink3, fontSize: TYPE.caption }}>서버에 남아 있는 연결 작업을 확인하고 재개하거나 해제합니다.</span>
          </span>
          <button aria-label="연결 상태 새로고침" className="product-focusable product-control" onClick={onRefresh} type="button" style={{ width: 34, height: 34, display: "grid", placeItems: "center", border: `1px solid ${UI.line}`, borderRadius: 10, background: UI.card, color: UI.ink2 }}>
            <RefreshCw size={15} />
          </button>
          <button aria-label="연결 관리 닫기" className="product-focusable product-control" onClick={onClose} type="button" style={{ width: 34, height: 34, display: "grid", placeItems: "center", border: 0, borderRadius: 999, background: UI.bg2, color: UI.ink2 }}>
            <X size={17} />
          </button>
        </header>

        <div style={{ minHeight: 0, overflowY: "auto", overscrollBehavior: "contain", scrollbarGutter: "stable", padding: 22 }}>
          <div style={{ display: "grid", gap: SPACE.section }}>
            <section style={{ display: "grid", gap: 12 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 8, color: UI.ink, fontSize: TYPE.body, fontWeight: 700 }}>
                  <Server size={15} style={{ color: BLUE }} />클러스터
                  <span style={{ color: UI.ink3, fontWeight: 500 }}>{targetClusters.length}</span>
                </span>
                <button className="product-focusable product-action" onClick={onConnectCluster} type="button" style={{ border: 0, borderRadius: 9, background: BLUE, color: UI.card, padding: "7px 11px", fontSize: TYPE.label, fontWeight: 700 }}>
                  + 클러스터 연결
                </button>
              </div>

              {selectedCluster ? (
                <div style={{ display: "grid", gap: 13, border: `1px solid ${UI.line}`, borderRadius: RADIUS.card, background: UI.card, padding: 15 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
                    <span style={{ minWidth: 0, flex: 1 }}>
                      <strong title={selectedCluster.displayName} style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: UI.ink, fontSize: TYPE.body }}>{selectedCluster.displayName}</strong>
                      <span style={{ display: "block", marginTop: 2, color: UI.ink3, fontSize: TYPE.caption, fontFamily: "ui-monospace, SFMono-Regular, monospace" }}>{selectedCluster.id}</span>
                    </span>
                    <span style={{ border: `1px solid ${stageTone(selectedCluster).border}`, borderRadius: 999, background: stageTone(selectedCluster).bg, color: stageTone(selectedCluster).fg, padding: "4px 9px", fontSize: TYPE.caption, fontWeight: 700 }}>
                      {stageLabel(selectedCluster)}
                    </span>
                    {canResumeClusterConnection(selectedCluster) ? (
                      <button className="product-focusable product-control" onClick={() => onResumeCluster(selectedCluster)} type="button" style={{ border: `1px solid ${TINT.blue.bd}`, borderRadius: 9, background: TINT.blue.bg, color: BLUE, padding: "6px 10px", fontSize: TYPE.label, fontWeight: 700 }}>
                        설치 재개
                      </button>
                    ) : null}
                    <ClusterLifecycleControl
                      cluster={selectedCluster}
                      onDisconnected={onDisconnected}
                      onPhaseChange={onLifecyclePhase}
                      roles={roles}
                    />
                  </div>
                  <ProgressNodeRail
                    ariaLabel={`${selectedCluster.displayName} 연결 진행`}
                    steps={clusterConnectionProgress(selectedCluster).map((step) =>
                      step.actionLabel === "설치 재개"
                        ? { ...step, onAction: () => onResumeCluster(selectedCluster) }
                        : step)}
                  />
                </div>
              ) : (
                <div style={{ border: `1px dashed ${UI.line}`, borderRadius: RADIUS.card, padding: 18, color: UI.ink3, fontSize: TYPE.label }}>연결된 대상 클러스터 없음</div>
              )}

              {targetClusters.length > 1 ? (
                <div aria-label="클러스터 연결 목록" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 8 }}>
                  {targetClusters.map((cluster) => {
                    const selected = cluster.id === selectedCluster?.id;
                    const tone = stageTone(cluster);
                    return (
                      <button
                        aria-pressed={selected}
                        className="product-focusable product-control"
                        key={cluster.id}
                        onClick={() => setSelectedClusterId(cluster.id)}
                        type="button"
                        style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 9, border: `1px solid ${selected ? BLUE : UI.line}`, borderRadius: 10, background: selected ? TINT.blue.bg : UI.card, padding: "9px 10px", textAlign: "left" }}
                      >
                        <span style={{ width: 22, height: 22, display: "grid", placeItems: "center", borderRadius: 999, background: tone.bg, color: tone.fg }}>
                          {cluster.connectionStage === "ready" ? <Check size={13} strokeWidth={3} /> : <Server size={12} />}
                        </span>
                        <span style={{ minWidth: 0, flex: 1 }}>
                          <strong style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: UI.ink, fontSize: TYPE.label }}>{cluster.displayName}</strong>
                          <span style={{ display: "block", marginTop: 1, color: tone.fg, fontSize: TYPE.caption }}>{stageLabel(cluster)}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </section>

            <section style={{ display: "grid", gap: 12 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 8, color: UI.ink, fontSize: TYPE.body, fontWeight: 700 }}>
                  <GithubIcon size={15} style={{ color: UI.ink }} />Git 저장소
                </span>
                <button className="product-focusable product-control" onClick={onConnectRepository} type="button" style={{ border: `1px solid ${UI.line}`, borderRadius: 9, background: UI.card, color: UI.ink, padding: "7px 11px", fontSize: TYPE.label, fontWeight: 700 }}>
                  + 저장소 연결
                </button>
              </div>
              <RepositoryStatusList onChanged={onRefresh} onConnect={() => onConnectRepository()} />
            </section>
          </div>
        </div>

        <footer style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: 8, borderTop: `1px solid ${UI.line}`, background: UI.card, padding: "11px 22px", color: UI.ink3, fontSize: TYPE.caption }}>
          <span style={{ width: 7, height: 7, borderRadius: 999, background: HP.ok }} />
          새 등록과 기존 작업 재개는 분리되며, 재개 시 기존 식별자를 유지합니다.
        </footer>
      </motion.section>
    </motion.div>
  );
}
