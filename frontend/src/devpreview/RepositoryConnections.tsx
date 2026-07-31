import { useState } from "react";

import { disconnectRepository } from "../api/repository-connection";
import { GithubIcon } from "./brandIcons";
import { ResourceAuxiliaryRow } from "./ResourceAuxiliaryPanel";
import type { RepositoryGroup } from "./repositoryRegistry";
import { HP, TINT, TYPE, UI, critA } from "./theme";

const UNHEALTHY_REPOSITORY_STATUSES = new Set([
  "outofsync", "out_of_sync", "failed", "error", "degraded", "unhealthy",
]);

function repositorySummary(group: RepositoryGroup): {
  branch: string;
  status: string;
  statusColor: string;
} {
  const branch = group.applications.find((application) => application.branch)?.branch
    ?? "브랜치 관측 안 됨";
  const statuses = group.applications.flatMap((application) => [
    application.deliveryStatus,
    application.healthStatus,
  ]).filter((status): status is string => Boolean(status?.trim()));
  const rawStatus = statuses.find((status) =>
    UNHEALTHY_REPOSITORY_STATUSES.has(status.toLowerCase().replace(/-/g, "_")),
  ) ?? statuses[0] ?? "상태 관측 안 됨";
  const normalized = rawStatus.toLowerCase().replace(/-/g, "_");
  const unhealthy = UNHEALTHY_REPOSITORY_STATUSES.has(normalized);
  const label = ({
    synced: "동기화됨",
    outofsync: "동기화 필요",
    out_of_sync: "동기화 필요",
    healthy: "정상",
    degraded: "성능 저하",
    unhealthy: "비정상",
    failed: "실패",
    error: "오류",
    pending: "대기 중",
    progressing: "진행 중",
  } as Record<string, string>)[normalized] ?? rawStatus;
  return {
    branch,
    status: label,
    statusColor: unhealthy ? TINT.warn.fg : UI.ink3,
  };
}

/**
 * Repository-level summary backed by observed application bindings.
 *
 * 연결 해제는 서버의 capability-gated `POST /repositories/disconnect` 계약으로
 * 지원된다. 해제는 repo·watch·binding·application 을 한 트랜잭션에서 비활성으로
 * 내리므로(고아 없음), 해제 후 부모가 목록을 새로고침하면 자연히 사라진다.
 */
export function RepositoryConnections({
  groups,
  onOpenRepository,
  onDisconnected,
  selectedRepository,
  expandedRepositories,
  onHoverRepository,
}: {
  groups: readonly RepositoryGroup[];
  onOpenRepository?: (repositoryRef: string) => void;
  onDisconnected?: (repositoryRef: string) => void;
  selectedRepository?: string | null;
  expandedRepositories?: readonly string[];
  onHoverRepository?: (group: RepositoryGroup | null) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      {groups.map((group) => {
        const repositoryKey = group.repositoryRef.toLowerCase();
        const selected = expandedRepositories
          ? expandedRepositories.some((repositoryRef) => repositoryRef.toLowerCase() === repositoryKey)
          : selectedRepository?.toLowerCase() === repositoryKey;
        return (
          <RepositoryRow
            key={group.repositoryRef}
            group={group}
            selected={Boolean(selected)}
            // 아코디언(여닫기)은 expandedRepositories 를 관리하는 배포 화면에서만 —
            // 사이드패널처럼 클릭이 화면 이동인 곳에서는 ▾ 토글을 그리지 않는다.
            accordion={expandedRepositories !== undefined}
            onOpenRepository={onOpenRepository}
            onDisconnected={onDisconnected}
            onHoverRepository={onHoverRepository}
          />
        );
      })}
    </div>
  );
}

function RepositoryRow({
  group,
  selected,
  accordion,
  onOpenRepository,
  onDisconnected,
  onHoverRepository,
}: {
  group: RepositoryGroup;
  selected: boolean;
  accordion: boolean;
  onOpenRepository?: (repositoryRef: string) => void;
  onDisconnected?: (repositoryRef: string) => void;
  onHoverRepository?: (group: RepositoryGroup | null) => void;
}) {
  const applicationsId = `repository-${group.repositoryRef.replace(/[^a-zA-Z0-9_-]/g, "-")}-applications`;
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const summary = repositorySummary(group);

  const runDisconnect = async () => {
    setBusy(true);
    setError(null);
    try {
      await disconnectRepository(group.repositoryRef);
      onDisconnected?.(group.repositoryRef);
      // 성공 시 부모가 목록을 새로고침하므로 이 행은 사라진다.
    } catch {
      setError("연결 해제에 실패했습니다. 권한을 확인하세요.");
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <div>
      <ResourceAuxiliaryRow
        className="product-control"
        aria-expanded={accordion ? selected : undefined}
        aria-controls={accordion ? applicationsId : undefined}
        ariaLabel={accordion
          ? `${group.repositoryRef} GitOps ${selected ? "닫기" : "열기"}`
          : `${group.repositoryRef} 배포 화면에서 열기`}
        onActivate={onOpenRepository ? () => onOpenRepository(group.repositoryRef) : undefined}
        onMouseEnter={() => onHoverRepository?.(group)}
        onMouseLeave={() => onHoverRepository?.(null)}
        onFocus={() => onHoverRepository?.(group)}
        onBlur={() => onHoverRepository?.(null)}
        data-pod-highlight-source="repository"
        selected={selected}
        icon={<GithubIcon size={16} aria-hidden="true" />}
        title={group.repositoryRef}
        tooltip={group.repositoryRef}
        meta={<span style={{ color: summary.statusColor }}>{summary.branch} · {summary.status}</span>}
        trailing={<>
          <span aria-label={`애플리케이션 ${group.applications.length}개`}>{group.applications.length}</span>
          {accordion && <span aria-hidden="true" style={{ color: selected ? "inherit" : UI.ink2, fontFamily: "inherit", fontSize: TYPE.body, transform: selected ? "rotate(180deg)" : "none", transition: "transform 150ms ease" }}>
            ▾
          </span>}
        </>}
      />

      {selected && (
        <ul
          id={applicationsId}
          style={{ display: "grid", gap: 4, margin: "4px 0 7px", padding: "0 7px 0 31px", listStyle: "none" }}
        >
          {group.applications.map((application) => (
            <li
              key={application.id}
              style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, minWidth: 0, padding: "9px 10px", border: `1px solid ${UI.line}`, borderRadius: 8, background: UI.card }}
            >
              <span style={{ minWidth: 0 }}>
                <strong style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: UI.ink, fontSize: TYPE.label }}>
                  {application.name}
                </strong>
                <span style={{ display: "block", marginTop: 2, overflow: "hidden", color: UI.ink3, fontSize: TYPE.caption, textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {application.manifestPath ?? "매니페스트 경로 관측 안 됨"}
                </span>
              </span>
              <span style={{ flex: "0 0 auto", color: UI.ink3, fontSize: TYPE.caption }}>
                {application.branch ?? "브랜치 관측 안 됨"}
              </span>
            </li>
          ))}

          {/* 연결 해제 — 인라인 2단계 확인(브라우저 다이얼로그 미사용). */}
          <li style={{ listStyle: "none", marginTop: 2 }}>
            {error && (
              <div role="alert" style={{ marginBottom: 6, color: TINT.crit.fg, fontSize: TYPE.caption }}>
                {error}
              </div>
            )}
            {!confirming ? (
              <button
                type="button"
                className="product-focusable product-destructive"
                onClick={() => setConfirming(true)}
                style={{
                  width: "100%",
                  padding: "8px 10px",
                  border: `1px solid ${TINT.crit.bd}`,
                  borderRadius: 8,
                  background: UI.card,
                  color: TINT.crit.fg,
                  fontSize: TYPE.label,
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                이 저장소 연결 해제
              </button>
            ) : (
              <div style={{ display: "grid", gap: 6 }}>
                <span style={{ color: TINT.crit.fg, fontSize: TYPE.caption, lineHeight: 1.45 }}>
                  해제하면 이 저장소의 폴링·동기화가 멈추고 앱 {group.applications.length}개가
                  목록에서 내려갑니다. 저장된 자격증명도 삭제됩니다.
                </span>
                <div style={{ display: "flex", gap: 6 }}>
                  <button
                    type="button"
                    className="product-focusable product-destructive"
                    disabled={busy}
                    onClick={() => void runDisconnect()}
                    style={{
                      flex: 1,
                      padding: "8px 10px",
                      border: 0,
                      borderRadius: 8,
                      background: busy ? critA(0.55) : HP.crit,
                      color: UI.card,
                      fontSize: TYPE.label,
                      fontWeight: 600,
                    }}
                  >
                    {busy ? "해제 중…" : "해제 확정"}
                  </button>
                  <button
                    type="button"
                    className="product-focusable product-control"
                    disabled={busy}
                    onClick={() => setConfirming(false)}
                    style={{
                      flex: 1,
                      padding: "8px 10px",
                      border: `1px solid ${UI.line}`,
                      borderRadius: 8,
                      background: UI.card,
                      color: UI.ink2,
                      fontSize: TYPE.label,
                      fontWeight: 600,
                    }}
                  >
                    취소
                  </button>
                </div>
              </div>
            )}
          </li>
        </ul>
      )}
    </div>
  );
}
