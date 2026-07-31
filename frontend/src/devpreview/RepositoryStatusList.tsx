import { useCallback, useEffect, useState } from "react";

import {
  disconnectRepository,
  listRepositories,
} from "../api/repository-connection";
import type { RepositoryListItem } from "../api/repository-connection-schemas";
import { isAbortError } from "../shared/data/asyncResourceState";

/**
 * 연결 상태 관리 목록 — active 뷰와 달리 degraded/disconnected 저장소까지 포함해
 * '연결은 남았는데 상태가 나쁜' 것을 한눈에 보여주고, 해제할 수 있게 한다.
 * (고아 없는 상태 설계의 가시화)
 */

const STATUS_STYLE: Record<
  RepositoryListItem["repository_status"],
  { label: string; fg: string; bg: string }
> = {
  active: { label: "연결됨", fg: "#16803b", bg: "rgba(34,197,94,.12)" },
  invalid_credential: { label: "자격증명 오류", fg: "#b45309", bg: "rgba(245,158,11,.14)" },
  source_unreachable: { label: "소스 접근 불가", fg: "#b45309", bg: "rgba(245,158,11,.14)" },
  disabled: { label: "비활성", fg: "#6b7280", bg: "rgba(120,120,120,.12)" },
  disconnected: { label: "연결 해제됨", fg: "#6b7280", bg: "rgba(120,120,120,.12)" },
  unknown: { label: "알 수 없음", fg: "#6b7280", bg: "rgba(120,120,120,.12)" },
};

export function RepositoryStatusList({ onChanged, onConnect }: {
  onChanged?: () => void;
  onConnect?: (repoRef: string) => void;
}) {
  const [items, setItems] = useState<RepositoryListItem[] | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [busyRef, setBusyRef] = useState<string | null>(null);
  const [confirmingRef, setConfirmingRef] = useState<string | null>(null);

  const load = useCallback((signal?: AbortSignal) => {
    setStatus("loading");
    listRepositories(signal)
      .then((response) => {
        if (signal?.aborted) return;
        setItems(response.repositories);
        setStatus("ready");
      })
      .catch((cause: unknown) => {
        if (signal?.aborted || isAbortError(cause)) return;
        setStatus("error");
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    // Start after the effect has subscribed so React does not receive a
    // synchronous loading-state write from the effect body.
    void Promise.resolve().then(() => {
      if (!controller.signal.aborted) load(controller.signal);
    });
    return () => controller.abort();
  }, [load]);

  const disconnect = async (repoRef: string) => {
    setBusyRef(repoRef);
    try {
      await disconnectRepository(repoRef);
      setConfirmingRef(null);
      onChanged?.();
      load();
    } catch {
      // 실패 시 목록만 다시 읽어 최신 상태로 맞춘다.
      load();
    } finally {
      setBusyRef(null);
    }
  };

  if (status === "loading" && items === null) {
    return <div style={{ padding: "10px 12px", color: "#9aa0aa", fontSize: 12 }}>연결 상태 불러오는 중…</div>;
  }
  if (status === "error") {
    return (
      <div style={{ padding: "10px 12px", color: "#b91c1c", fontSize: 12 }}>
        연결 상태를 불러오지 못했습니다.
      </div>
    );
  }
  if (!items || items.length === 0) {
    return <div style={{ padding: "10px 12px", color: "#9aa0aa", fontSize: 12 }}>연결된 저장소 없음</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {items.map((item) => {
        const style = STATUS_STYLE[item.repository_status];
        const terminal = item.repository_status === "disconnected";
        return (
          <div
            key={item.repository_id}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 10,
              minWidth: 0,
              padding: "10px 12px",
              border: "1px solid #e5e7eb",
              borderRadius: 9,
              background: "#fff",
            }}
          >
            <span style={{ minWidth: 0, flex: 1 }}>
              <strong
                title={item.repo_ref}
                style={{
                  display: "block",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  color: "#111827",
                  fontSize: 12.5,
                }}
              >
                {item.repo_ref}
              </strong>
              <span style={{ display: "block", marginTop: 2, color: "#9aa0aa", fontSize: 11 }}>
                앱 {item.application_count}개{item.default_branch ? ` · ${item.default_branch}` : ""}
              </span>
            </span>
            <span
              style={{
                flexShrink: 0,
                padding: "3px 9px",
                borderRadius: 999,
                fontSize: 11,
                fontWeight: 700,
                color: style.fg,
                background: style.bg,
              }}
            >
              {style.label}
            </span>
            {terminal || item.repository_status !== "active" ? (
              onConnect ? (
                <button
                  type="button"
                  onClick={() => onConnect(item.repo_ref)}
                  title="저장소 다시 연결"
                  style={{
                    flexShrink: 0,
                    padding: "6px 10px",
                    border: "1px solid #bfdbfe",
                    borderRadius: 8,
                    background: "#fff",
                    color: "#0a84ff",
                    fontSize: 11,
                    fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  다시 연결
                </button>
              ) : null
            ) : confirmingRef === item.repo_ref ? (
              <span style={{ display: "inline-flex", flexShrink: 0, gap: 4 }}>
                <button
                  type="button"
                  disabled={busyRef === item.repo_ref}
                  onClick={() => void disconnect(item.repo_ref)}
                  style={{
                    padding: "6px 9px",
                    border: 0,
                    borderRadius: 8,
                    background: "#b91c1c",
                    color: "#fff",
                    fontSize: 11,
                    fontWeight: 700,
                    cursor: busyRef === item.repo_ref ? "default" : "pointer",
                  }}
                >
                  {busyRef === item.repo_ref ? "해제 중…" : "해제 확정"}
                </button>
                <button
                  type="button"
                  disabled={busyRef === item.repo_ref}
                  onClick={() => setConfirmingRef(null)}
                  style={{
                    padding: "6px 9px",
                    border: "1px solid #e5e7eb",
                    borderRadius: 8,
                    background: "#fff",
                    color: "#6b7280",
                    fontSize: 11,
                    fontWeight: 600,
                  }}
                >
                  취소
                </button>
              </span>
            ) : (
              <button
                type="button"
                disabled={busyRef === item.repo_ref}
                onClick={() => setConfirmingRef(item.repo_ref)}
                title="연결 해제"
                style={{
                  flexShrink: 0,
                  padding: "6px 10px",
                  border: "1px solid #fca5a5",
                  borderRadius: 8,
                  background: "#fff",
                  color: "#b91c1c",
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: busyRef === item.repo_ref ? "default" : "pointer",
                }}
              >
                연결 해제
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
