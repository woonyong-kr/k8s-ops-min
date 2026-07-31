import { useEffect, useRef, useState } from "react";
import { Check, ExternalLink } from "lucide-react";

import { getAuditTimeline } from "../api/audit-timeline";
import {
  applyResourceManifestEdit,
  approveResourceManifestEdit,
  getCommandStatus,
  getResourceManifestSource,
  isResourceManifestSourceConflict,
  manifestIdempotencyKey,
  previewResourceManifestEdit,
  resourceManifestFailureRemediation,
  resourceManifestFailureText,
  resourceManifestSourceRemediation,
  type CommandStatus,
  type ResourceManifestApplyEndpoint,
  type ResourceManifestApproveEndpoint,
  type ResourceManifestPreviewEndpoint,
  type ResourceManifestSourceEndpoint,
  type ResourceManifestRemediation,
} from "./resourceManifestFeed";

export const RESOURCE_MANIFEST_COMMAND_POLL_MS = 1_500;
import { grantApproval, rejectApproval } from "../api/approvals";
import { reasonLabel } from "./statusLabel";
import { Spinner } from "../shared/ui/primitives/spinner";
import { BLUE, HP, MONO, TINT, TYPE, UI, inkA } from "./theme";
import { DiffCodeView, YamlCodeView } from "./YamlCodeView";

type Phase = "loading" | "ready" | "previewing" | "submitting" | "failed";

interface LiveResourceManifestEditorProps {
  resourceId: string;
  resolving?: boolean;
  refreshKey?: number;
  /** 읽기 패널에서 이미 조회한 소스. 보조 편집 패널의 중복 조회와 깜박임을 막는다. */
  initialSource?: ResourceManifestSourceEndpoint | null;
  /** 조회한 소스를 나란히 열리는 편집 패널과 공유한다. */
  onSourceLoaded?: (source: ResourceManifestSourceEndpoint) => void;
  /** 읽기 전용 상세과 보조 편집 패널을 분리할 때 사용하는 표시 모드. */
  mode?: "read" | "edit";
  /** 읽기 전용 화면의 편집 요청을 부모 보조 패널로 전달한다. */
  onEditRequest?: () => void;
  /** 확장(전체 화면) 모드 — 에디터|관측·diff 2열 레이아웃으로 전환. */
  wide?: boolean;
  /** 편집 가능한 Git 원본이 열렸는지 통지 — 부모가 패널 자동 확장에 사용. */
  onEditableChange?: (editable: boolean) => void;
  onConnectRepository?: () => void;
  onOpenDeploySurface?: () => void;
  onReauthenticate?: () => void;
  onRequestAccess?: () => void;
}

export function LiveResourceManifestEditor({
  resourceId,
  resolving = false,
  refreshKey = 0,
  initialSource = null,
  onSourceLoaded,
  mode,
  onEditRequest,
  wide = false,
  onEditableChange,
  onConnectRepository,
  onOpenDeploySurface,
  onReauthenticate,
  onRequestAccess,
}: LiveResourceManifestEditorProps) {
  const hasInitialSource = initialSource?.resource_id === resourceId;
  const [phase, setPhase] = useState<Phase>(hasInitialSource ? "ready" : "loading");
  const [source, setSource] = useState<ResourceManifestSourceEndpoint | null>(
    hasInitialSource ? initialSource : null,
  );
  const [applicationId, setApplicationId] = useState(
    hasInitialSource ? initialSource.selected?.application_id ?? "" : "",
  );
  const [yaml, setYaml] = useState(
    hasInitialSource ? restoreManifestDraft(resourceId, initialSource) : "",
  );
  const [preview, setPreview] = useState<ResourceManifestPreviewEndpoint | null>(null);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [approval, setApproval] = useState<ResourceManifestApproveEndpoint | null>(null);
  const [approvalDecision, setApprovalDecision] = useState<"granted" | "rejected" | null>(null);
  const [approvalDecisionBusy, setApprovalDecisionBusy] = useState(false);
  const [approvalDecisionError, setApprovalDecisionError] = useState<string | null>(null);
  const [emergencyApproval, setEmergencyApproval] = useState<ResourceManifestApproveEndpoint | null>(null);
  const [applyReceipt, setApplyReceipt] = useState<ResourceManifestApplyEndpoint | null>(null);
  const [applyStatus, setApplyStatus] = useState<CommandStatus | null>(null);
  const [manifestPrUrl, setManifestPrUrl] = useState<string | null>(null);
  const [manifestPrFailed, setManifestPrFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failureRemediation, setFailureRemediation] = useState<ResourceManifestRemediation>("none");
  const [sourceConflictNotice, setSourceConflictNotice] = useState<string | null>(null);
  const [sourceRefreshRequired, setSourceRefreshRequired] = useState(false);
  const [editingSelf, setEditingSelf] = useState(false);
  const [draftSaved, setDraftSaved] = useState(false);
  const controller = useRef<AbortController | null>(null);

  const load = async (selectedApplicationId?: string | null) => {
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    setPhase("loading");
    setError(null);
    setFailureRemediation("none");
    setSourceConflictNotice(null);
    setSourceRefreshRequired(false);
    setPreview(null);
    setApproval(null);
    setEmergencyApproval(null);
    setApplyReceipt(null);
    setApplyStatus(null);
    setManifestPrUrl(null);
    setManifestPrFailed(false);
    setEditingSelf(false);
    try {
      const loaded = await getResourceManifestSource(resourceId, selectedApplicationId, next.signal);
      if (next.signal.aborted) return;
      setSource(loaded);
      onSourceLoaded?.(loaded);
      setApplicationId(loaded.selected?.application_id ?? selectedApplicationId ?? "");
      setYaml(restoreManifestDraft(resourceId, loaded));
      setDraftSaved(false);
      setPhase("ready");
    } catch (cause) {
      if (next.signal.aborted) return;
      setError(resourceManifestFailureText(cause));
      setFailureRemediation(resourceManifestFailureRemediation(cause));
      setPhase("failed");
    }
  };

  useEffect(() => {
    controller.current?.abort();
    if (!resourceId) return;
    const next = new AbortController();
    controller.current = next;
    void (async () => {
      // Defer prop-to-editor synchronization out of the effect body. This
      // preserves the supplied source without a synchronous render cascade.
      await Promise.resolve();
      if (next.signal.aborted) return;
      setEditingSelf(false);
      if (initialSource?.resource_id === resourceId) {
        setSource(initialSource);
        setApplicationId(initialSource.selected?.application_id ?? "");
        setYaml(restoreManifestDraft(resourceId, initialSource));
        setPhase("ready");
        return;
      }
      try {
        const loaded = await getResourceManifestSource(resourceId, null, next.signal);
        if (next.signal.aborted) return;
        setSource(loaded);
        onSourceLoaded?.(loaded);
        setApplicationId(loaded.selected?.application_id ?? "");
        setYaml(restoreManifestDraft(resourceId, loaded));
        setDraftSaved(false);
        setPreview(null);
        setApproval(null);
        setEmergencyApproval(null);
        setApplyReceipt(null);
        setApplyStatus(null);
        setManifestPrUrl(null);
        setManifestPrFailed(false);
        setError(null);
        setFailureRemediation("none");
        setSourceConflictNotice(null);
        setSourceRefreshRequired(false);
        setPhase("ready");
      } catch (cause) {
        if (next.signal.aborted) return;
        setSource(null);
        setError(resourceManifestFailureText(cause));
        setFailureRemediation(resourceManifestFailureRemediation(cause));
        setPhase("failed");
      }
    })();
    return () => next.abort();
    // initialSource/onSourceLoaded는 첫 마운트에 사용하는 전달값이다. 이후 새로고침은
    // refreshKey/resourceId 변경으로만 수행해 편집 중 원본이 되감기지 않게 한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey, resourceId]);

  useEffect(() => {
    if (!applyReceipt?.command_id) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const next = await getCommandStatus(applyReceipt.command_id, abort.signal);
        if (abort.signal.aborted) return;
        setApplyStatus(next);
        if (next.status !== "completed" && next.status !== "failed") {
          timer = setTimeout(() => void poll(), RESOURCE_MANIFEST_COMMAND_POLL_MS);
        }
      } catch {
        // The immutable receipt remains visible. A transient status read must not
        // turn a successfully queued operation into a fabricated failure.
      }
    };
    void poll();
    return () => {
      abort.abort();
      if (timer !== null) clearTimeout(timer);
    };
  }, [applyReceipt?.command_id]);

  const manifestCorrelationId = approval?.correlation_id
    ?? emergencyApproval?.correlation_id
    ?? null;
  useEffect(() => {
    if (!manifestCorrelationId || approvalDecision === "rejected") return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const timeline = await getAuditTimeline(manifestCorrelationId, {
          limit: 200,
          signal: abort.signal,
        });
        if (abort.signal.aborted) return;
        const created = [...timeline.items].reverse().find(
          (item) => item.subject.replace(/[.-]/gu, "_").toLowerCase() === "safe_pr_created",
        );
        const failed = [...timeline.items].reverse().find(
          (item) => item.subject.replace(/[.-]/gu, "_").toLowerCase() === "safe_pr_failed",
        );
        const prUrl = created && typeof created.payload_summary.pr_url === "string"
          ? created.payload_summary.pr_url
          : null;
        if (prUrl) {
          setManifestPrUrl(prUrl);
          setManifestPrFailed(false);
          return;
        }
        if (failed) {
          setManifestPrFailed(true);
          return;
        }
        timer = setTimeout(() => void poll(), 2_000);
      } catch {
        if (!abort.signal.aborted) timer = setTimeout(() => void poll(), 3_000);
      }
    };
    void poll();
    return () => {
      abort.abort();
      if (timer !== null) clearTimeout(timer);
    };
  }, [approvalDecision, manifestCorrelationId]);

  const sourceIsCurrent = source?.resource_id === resourceId;
  const editInput = sourceIsCurrent && source?.base_sha && source.source_sha256 && applicationId && yaml
    ? {
        applicationId,
        baseSha: source.base_sha,
        sourceSha256: source.source_sha256,
        sourceRevisionToken: source.source_revision_token,
        editedYaml: yaml,
      }
    : null;
  const busy = phase === "loading" || phase === "previewing" || phase === "submitting";
  const canEdit = Boolean(
    source && source.status === "available" && source.selected && source.content,
  );
  // 사용자가 편집을 명시적으로 시작했을 때만 부모 패널을 확장한다.
  const editing = mode === "edit" || (mode !== "read" && editingSelf);
  const editable = canEdit && editing;
  useEffect(() => {
    onEditableChange?.(editable);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 콜백 아이덴티티가 아니라 editable 변화에만 반응
  }, [editable]);

  const invalidateSourceBoundState = () => {
    setPreview(null);
    setConfirmed(false);
    setApproval(null);
    setApprovalDecision(null);
    setApprovalDecisionError(null);
    setEmergencyApproval(null);
    setApplyReceipt(null);
    setApplyStatus(null);
    setManifestPrUrl(null);
    setManifestPrFailed(false);
  };

  const saveDraft = () => {
    if (!source?.source_sha256 || !source.selected) return;
    window.localStorage.setItem(manifestDraftKey(resourceId), JSON.stringify({
      applicationId: source.selected.application_id,
      sourceSha256: source.source_sha256,
      yaml,
    }));
    setDraftSaved(true);
  };

  const refreshLatestSourcePreservingYaml = async () => {
    controller.current?.abort();
    const next = new AbortController();
    controller.current = next;
    setSourceRefreshRequired(true);
    setPhase("submitting");
    setError(null);
    setFailureRemediation("none");
    try {
      const loaded = await getResourceManifestSource(
        resourceId,
        applicationId || source?.selected?.application_id || null,
        next.signal,
      );
      if (next.signal.aborted) return;
      if (
        loaded.status !== "available"
        || loaded.selected === null
        || loaded.base_sha === null
        || loaded.source_sha256 === null
        || loaded.content === null
      ) {
        throw new Error("최신 Git 원본을 편집 가능한 상태로 확인하지 못했습니다.");
      }
      setSource(loaded);
      setApplicationId(loaded.selected.application_id);
      setSourceRefreshRequired(false);
      setSourceConflictNotice(
        "Git 원본이 변경되어 최신 기준을 다시 불러왔습니다. 작성 중인 YAML은 보존했지만 이전 미리보기와 확인은 무효화했습니다. 변경 검증·미리보기를 다시 실행하고 확인한 뒤 요청하세요.",
      );
      setPhase("ready");
    } catch (cause) {
      if (next.signal.aborted) return;
      setError(`최신 Git 원본을 다시 불러오지 못했습니다. ${resourceManifestFailureText(cause)}`);
      setPhase("failed");
    }
  };

  const recoverSourceConflict = async (cause: unknown): Promise<boolean> => {
    if (!isResourceManifestSourceConflict(cause)) return false;
    invalidateSourceBoundState();
    setSourceConflictNotice(
      "Git 원본이 편집 중 변경되었습니다. 작성 중인 YAML을 유지한 채 최신 기준을 다시 불러오고 있습니다.",
    );
    await refreshLatestSourcePreservingYaml();
    return true;
  };

  const runPreview = async () => {
    if (!editInput) return;
    setPhase("previewing");
    setError(null);
    setApproval(null);
    setApplyReceipt(null);
    try {
      setPreview(await previewResourceManifestEdit(resourceId, editInput));
      setSourceConflictNotice(null);
      setPhase("ready");
    } catch (cause) {
      if (await recoverSourceConflict(cause)) return;
      setError(resourceManifestFailureText(cause));
      setPhase("failed");
    }
  };

  const submitSafePr = async () => {
    if (!editInput || !preview?.valid || reason.trim().length < 3) return;
    setPhase("submitting");
    setError(null);
    setManifestPrUrl(null);
    setManifestPrFailed(false);
    try {
      setApprovalDecision(null);
      setApprovalDecisionError(null);
      setApproval(await approveResourceManifestEdit(resourceId, {
        ...editInput,
        confirmed: true,
        reason: reason.trim(),
      }));
      setPhase("ready");
    } catch (cause) {
      if (await recoverSourceConflict(cause)) return;
      setError(resourceManifestFailureText(cause));
      setPhase("failed");
    }
  };

  const decideApproval = async (decision: "granted" | "rejected") => {
    if (!approval || approvalDecisionBusy) return;
    setApprovalDecisionBusy(true);
    setApprovalDecisionError(null);
    try {
      if (decision === "granted") {
        await grantApproval(approval.approval_id, { reason: reason.trim() || null });
      } else {
        await rejectApproval(approval.approval_id, { reason: reason.trim() || null });
      }
      setApprovalDecision(decision);
    } catch (cause) {
      setApprovalDecisionError(resourceManifestFailureText(cause));
    } finally {
      setApprovalDecisionBusy(false);
    }
  };

  const submitDirectApply = async () => {
    if (
      !editInput || !preview?.valid || preview.apply_availability !== "available" ||
      reason.trim().length < 3 || !confirmed
    ) return;
    setPhase("submitting");
    setError(null);
    try {
      const recorded = emergencyApproval ?? await approveResourceManifestEdit(resourceId, {
        ...editInput,
        confirmed: true,
        reason: reason.trim(),
      });
      setEmergencyApproval(recorded);
      setApplyStatus(null);
      setApplyReceipt(await applyResourceManifestEdit(resourceId, {
        ...editInput,
        expectedDesiredSha256: preview.desired_sha256,
        confirmation: true,
        reason: reason.trim(),
        idempotencyKey: manifestIdempotencyKey(
          resourceId,
          preview.desired_sha256,
          preview.base_sha,
          preview.source_sha256,
        ),
      }));
      setPhase("ready");
    } catch (cause) {
      if (await recoverSourceConflict(cause)) return;
      setError(resourceManifestFailureText(cause));
      setPhase("failed");
    }
  };

  if (!resourceId && resolving) {
    return <ManifestLoadingState label="YAML 정체성 확인 중" wide={wide} />;
  }
  if (!resourceId) {
    return <ManifestNotice tone="warn" title="YAML 정체성 확인 불가">이 행에는 서버가 발급한 inventory key가 없습니다.</ManifestNotice>;
  }
  if (phase === "loading" || (!sourceIsCurrent && phase !== "failed")) {
    return <ManifestLoadingState label="YAML 소스 확인 중" wide={wide} />;
  }
  if (phase === "failed" && !source) {
    const title = failureRemediation === "reauthenticate"
      ? "로그인이 필요합니다"
      : failureRemediation === "request-access"
        ? "YAML 접근 권한이 없습니다"
        : "YAML 원본 조회 실패";
    return (
      <div style={{ padding: "18px 0", display: "grid", gap: 10 }}>
        <ManifestNotice tone="warn" title={title}>
          {failureRemediation === "reauthenticate"
            ? "세션을 다시 인증한 뒤 같은 리소스의 YAML을 조회하세요."
            : failureRemediation === "request-access"
              ? "워크스페이스 관리자에게 애플리케이션 매니페스트 조회 권한을 요청하세요."
              : error ?? "YAML 원본을 불러오지 못했습니다."}
        </ManifestNotice>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {failureRemediation === "reauthenticate" && onReauthenticate && (
            <ActionButton disabled={false} onClick={onReauthenticate}>다시 로그인</ActionButton>
          )}
          {failureRemediation === "request-access" && onRequestAccess && (
            <ActionButton disabled={false} onClick={onRequestAccess}>권한 요청</ActionButton>
          )}
          {failureRemediation === "retry" && (
            <ActionButton disabled={false} onClick={() => void load()}>다시 시도</ActionButton>
          )}
        </div>
      </div>
    );
  }
  if (source?.status === "ambiguous") {
    return (
      <div style={{ padding: "18px 0", display: "grid", gap: 10 }}>
        <LiveManifestPanel source={source} />
        <ManifestNotice tone="warn" title="애플리케이션 소스를 선택하세요">동일 리소스를 소유한 실제 Git 소스가 여러 개입니다.</ManifestNotice>
        <select className="product-focusable" aria-label="YAML 애플리케이션 소스" value={applicationId}
          onChange={(event) => { const id = event.currentTarget.value; setApplicationId(id); if (id) void load(id); }}
          style={selectStyle}>
          <option value="">애플리케이션 선택</option>
          {source.choices.map((choice) => (
            <option key={choice.application_id} value={choice.application_id}>
              {choice.application_name} · {choice.repository_ref}/{choice.manifest_path}
            </option>
          ))}
        </select>
      </div>
    );
  }
  if (!source || source.status !== "available" || !source.selected || !source.content) {
    const remediation = resourceManifestSourceRemediation(source?.reason ?? null);
    return (
      <div style={{ padding: "18px 0", display: "grid", gap: 10 }}>
        {source && (
          <LiveManifestPanel
            source={source}
            action={onConnectRepository ? (
              <ActionButton disabled={false} onClick={onConnectRepository}>편집</ActionButton>
            ) : undefined}
          />
        )}
        <ManifestNotice tone={remediation === "connect-repository" ? "neutral" : "warn"}
          title={remediation === "request-access" ? "YAML 접근 권한이 없습니다" : "Git에서 배포된 리소스가 아닙니다"}>
          {remediation === "connect-repository"
            ? "이 리소스는 연결된 저장소의 매니페스트와 매칭되지 않아 편집할 수 없습니다. " +
              "에이전트·시스템 구성 요소처럼 Git 밖에서 배포된 리소스는 읽기 전용입니다. " +
              "저장소에서 배포된 리소스는 이 탭에서 바로 수정하고 승인하면 실제 PR이 생성됩니다."
            : remediation === "request-access"
              ? "연결된 Git 원본이 있지만 현재 계정에는 애플리케이션 매니페스트 조회 권한이 없습니다."
              : source?.reason ? reasonLabel(source.reason) : error ?? "이 리소스에 연결된 현재 YAML 원본을 찾지 못했습니다."}
        </ManifestNotice>
        {remediation === "connect-repository" && onConnectRepository && (
          <ActionButton disabled={false} onClick={onConnectRepository}>다른 저장소 연결…</ActionButton>
        )}
        {remediation === "request-access" && onRequestAccess && (
          <ActionButton disabled={false} onClick={onRequestAccess}>권한 요청</ActionButton>
        )}
        {remediation === "none" && (
          <small style={{ color: UI.ink3, lineHeight: 1.5 }}>
            인벤토리 원문을 합성하지 않습니다. 활성 애플리케이션·배포 바인딩·GitHub raw YAML 연결이 있어야 편집할 수 있습니다.
          </small>
        )}
      </div>
    );
  }

  if (!editing) {
    return (
      <div style={{ padding: "16px 0 24px" }}>
        <LiveManifestPanel
          source={source}
          action={(
            <ActionButton
              primary
              disabled={false}
              onClick={() => {
                if (onEditRequest) onEditRequest();
                else setEditingSelf(true);
              }}
            >
              편집
            </ActionButton>
          )}
        />
      </div>
    );
  }

  const diffView = preview?.diff ? (
    <DiffCodeView value={preview.diff} ariaLabel="변경 diff 미리보기" maxHeight={null} />
  ) : null;
  const editColumn = (
    <div style={{ display: "grid", gap: 12, minWidth: 0 }}>
      <div style={{ display: "grid", gap: 4 }}>
        <b style={{ color: UI.ink, fontSize: TYPE.body }}>Git 원본 · IDE 편집</b>
        {source.edit_target && (
          <span style={{ color: UI.ink3, fontSize: TYPE.caption }}>
            {source.edit_target.relationship === "owner" ? "Owner " : ""}
            {source.edit_target.kind}/{source.edit_target.name}
          </span>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap", fontSize: TYPE.caption, color: UI.ink2 }}>
        <Pill>{source.selected.repository_ref}</Pill><Pill>{source.selected.branch}</Pill>
        <span style={{ flexBasis: "100%", fontFamily: MONO }}>{source.selected.manifest_path}</span>
        <span style={{ flexBasis: "100%", fontFamily: MONO, color: UI.ink3 }}>commit {source.base_sha?.slice(0, 12)}</span>
      </div>
      <textarea className="manifest-yaml-editor" aria-label="Git YAML 원본 편집기" value={yaml} disabled={busy || !!approval || !!emergencyApproval || !!applyReceipt}
        rows={Math.max(18, yaml.split("\n").length + 1)}
        wrap="off"
        onChange={(event) => { setYaml(event.currentTarget.value); setPreview(null); setConfirmed(false); setSourceConflictNotice(null); setDraftSaved(false); }} spellCheck={false}
        style={{ width: "100%", minHeight: wide ? 480 : 360, resize: "none", overflowX: "scroll", overflowY: "hidden", boxSizing: "border-box", border: `1px solid ${UI.line}`, borderRadius: 12, padding: 14, background: "#0d1117", color: "#e6edf3", fontFamily: MONO, fontSize: TYPE.code, lineHeight: 1.6, outline: "none" }} />
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <ActionButton primary disabled={busy || !editInput} onClick={saveDraft}>저장</ActionButton>
        <ActionButton disabled={busy || sourceRefreshRequired} onClick={() => void runPreview()}>
          {phase === "previewing" ? "검증 중…" : "변경 검증·미리보기"}
        </ActionButton>
        {draftSaved && <Pill tone="ok">임시 저장됨</Pill>}
        {preview && <Pill tone={preview.valid ? "ok" : "warn"}>{preview.valid ? "서버 검증 통과" : "YAML 오류"}</Pill>}
        {preview?.valid && <Pill tone={preview.apply_availability === "available" ? "ok" : "warn"}>
          {preview.apply_availability === "available" ? "즉시 적용 가능" : "Safe PR만 가능"}
        </Pill>}
      </div>
      {diffView}
      {preview?.errors.map((item) => <ManifestNotice key={item} tone="error" title="검증 오류">{item}</ManifestNotice>)}
      {preview?.warnings.map((item) => <ManifestNotice key={item} tone="warn" title="검토 필요">{item}</ManifestNotice>)}
      {preview?.apply_reason_codes.map((item) => <ManifestNotice key={item} tone="warn" title="즉시 적용 제한">{reasonLabel(item)}</ManifestNotice>)}
      {sourceConflictNotice && (
        <ManifestNotice tone="warn" title="Git 원본 변경 감지">
          {sourceConflictNotice}
          {sourceRefreshRequired && phase === "failed" && (
            <div style={{ marginTop: 8 }}>
              <ActionButton disabled={false} onClick={() => void refreshLatestSourcePreservingYaml()}>
                최신 Git 기준 다시 불러오기
              </ActionButton>
            </div>
          )}
        </ManifestNotice>
      )}
      {error && <ManifestNotice tone="error" title="YAML 요청 실패">{error}</ManifestNotice>}
      {preview?.valid && !approval && !applyReceipt && (
        <div style={{ display: "grid", gap: 9, borderTop: `1px solid ${UI.line2}`, paddingTop: 12 }}>
          <input aria-label="변경 사유" value={reason} onChange={(event) => setReason(event.currentTarget.value)} placeholder="변경 사유 (3자 이상)"
            style={{ border: `1px solid ${UI.line}`, borderRadius: 9, padding: "8px 10px", fontSize: TYPE.body, color: UI.ink, background: UI.card }} />
          {preview.apply_availability === "available" && (
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: TYPE.label, color: UI.ink2 }}>
              <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.currentTarget.checked)} />
              동일 Git artifact를 먼저 기록한 뒤 owner controller에 직접 적용하며 Git 동기화가 끝날 때까지 drift 상태가 남음을 확인합니다.
            </label>
          )}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <ActionButton primary disabled={busy || reason.trim().length < 3} onClick={() => void submitSafePr()}>Safe PR 요청</ActionButton>
            {preview.apply_availability === "available" && (
              <ActionButton disabled={busy || reason.trim().length < 3 || !confirmed} onClick={() => void submitDirectApply()}>
                {emergencyApproval ? "긴급 적용 재시도" : "Git 기록 후 긴급 적용"}
              </ActionButton>
            )}
          </div>
        </div>
      )}
      {(approval || emergencyApproval || applyReceipt) && (
        <ManifestWorkflowProgress
          route={emergencyApproval || applyReceipt ? "direct" : "safe-pr"}
          approval={approval}
          approvalDecision={approvalDecision}
          approvalDecisionBusy={approvalDecisionBusy}
          approvalDecisionError={approvalDecisionError}
          applyReceipt={applyReceipt}
          applyStatus={applyStatus}
          baseSha={source.base_sha}
          repositoryRef={source.selected.repository_ref}
          prUrl={manifestPrUrl}
          prFailed={manifestPrFailed}
          onApprovalDecision={decideApproval}
          onOpenDeploySurface={onOpenDeploySurface}
        />
      )}
    </div>
  );

  return <div style={{ padding: "16px 0 24px" }}>{editColumn}</div>;
}

const SAFE_PR_MANIFEST_STEPS = ["검증", "요청", "승인", "PR 생성", "완료"] as const;
const DIRECT_MANIFEST_STEPS = ["검증", "Git 기록", "명령 접수", "적용", "완료"] as const;

function ManifestWorkflowProgress({
  route,
  approval,
  approvalDecision,
  approvalDecisionBusy,
  approvalDecisionError,
  applyReceipt,
  applyStatus,
  baseSha,
  repositoryRef,
  prUrl,
  prFailed,
  onApprovalDecision,
  onOpenDeploySurface,
}: {
  route: "safe-pr" | "direct";
  approval: ResourceManifestApproveEndpoint | null;
  approvalDecision: "granted" | "rejected" | null;
  approvalDecisionBusy: boolean;
  approvalDecisionError: string | null;
  applyReceipt: ResourceManifestApplyEndpoint | null;
  applyStatus: CommandStatus | null;
  baseSha: string | null;
  repositoryRef: string;
  prUrl: string | null;
  prFailed: boolean;
  onApprovalDecision: (decision: "granted" | "rejected") => Promise<void>;
  onOpenDeploySurface?: () => void;
}) {
  const failed = prFailed
    || approvalDecision === "rejected"
    || applyStatus?.status === "failed";
  const completed = route === "safe-pr"
    ? Boolean(prUrl)
    : applyStatus?.status === "completed";
  const step = route === "safe-pr"
    ? prUrl ? 5 : approvalDecision === "granted" ? 3 : approval ? 2 : 1
    : applyStatus?.status === "completed" ? 5
      : applyStatus?.status === "running" || applyStatus?.status === "leased" ? 4
        : applyReceipt ? 3 : 2;
  const labels = route === "safe-pr" ? SAFE_PR_MANIFEST_STEPS : DIRECT_MANIFEST_STEPS;
  const activeColor = failed ? HP.crit : completed ? HP.ok : BLUE;
  const statusLabel = failed
    ? "중단"
    : completed
      ? "완료"
      : `${step}/5`;
  const latestRecord = manifestWorkflowRecord({
    route,
    approval,
    approvalDecision,
    applyReceipt,
    applyStatus,
    prUrl,
    prFailed,
  });
  const commitUrl = githubCommitUrl(repositoryRef, baseSha);

  return (
    <section
      aria-live="polite"
      style={{
        display: "grid",
        gap: 12,
        border: `1px solid ${UI.line}`,
        borderRadius: 10,
        background: UI.card,
        padding: 14,
        boxShadow: `0 6px 16px -10px ${inkA(0.26)}, 0 1px 3px ${inkA(0.06)}`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ minWidth: 0, flex: 1, display: "grid", gap: 3 }}>
          <strong style={{ color: UI.heading, fontSize: TYPE.section, fontWeight: 700 }}>
            {route === "safe-pr" ? "Safe PR 진행" : "긴급 적용 진행"}
          </strong>
          <span style={{ color: UI.ink3, fontSize: TYPE.label }}>YAML 변경 진행 상태</span>
        </div>
        <span style={{ flexShrink: 0, color: failed ? HP.crit : UI.ink2, fontSize: TYPE.body, fontVariantNumeric: "tabular-nums" }}>
          {statusLabel}
        </span>
      </div>

      <div aria-label="YAML 변경 진행률" style={{ height: 5, overflow: "hidden", borderRadius: 999, background: HP.pending }}>
        <div style={{ width: `${step * 20}%`, height: "100%", borderRadius: 999, background: activeColor, transition: "width 320ms ease" }} />
      </div>

      <ol aria-label="YAML 변경 진행 단계" style={{ display: "grid", gridTemplateColumns: "repeat(5, minmax(0, 1fr))", gap: 5, margin: 0, padding: "11px 0 0", borderTop: `1px dashed ${UI.line}`, listStyle: "none" }}>
        {labels.map((label, index) => {
          const position = index + 1;
          const done = completed || position < step;
          const active = !completed && position === step;
          return (
            <li key={label} style={{ minWidth: 0, display: "grid", justifyItems: "center", gap: 5, textAlign: "center" }}>
              <span
                aria-hidden="true"
                style={{
                  width: 22,
                  height: 22,
                  display: "grid",
                  placeItems: "center",
                  borderRadius: 6,
                  border: `1px solid ${active ? activeColor : UI.line}`,
                  background: active ? activeColor : done ? TINT.ok.bg : UI.card,
                  color: active ? UI.card : done ? TINT.ok.fg : UI.ink3,
                  fontSize: TYPE.caption,
                  fontWeight: 600,
                }}
              >
                {done ? <Check size={12} /> : position}
              </span>
              <span style={{ width: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: active ? UI.ink : UI.ink3, fontSize: TYPE.caption, fontWeight: active ? 600 : 500 }}>
                {label}
              </span>
            </li>
          );
        })}
      </ol>

      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: UI.ink3, fontSize: TYPE.caption }}>
        최근 기록 · {latestRecord}
      </span>

      {route === "safe-pr" && approval && approvalDecision === null && !prUrl && !prFailed && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <ActionButton primary disabled={approvalDecisionBusy} onClick={() => void onApprovalDecision("granted")}>
            승인하고 진행
          </ActionButton>
          <ActionButton disabled={approvalDecisionBusy} onClick={() => void onApprovalDecision("rejected")}>
            거부
          </ActionButton>
        </div>
      )}
      {approvalDecisionError && (
        <span role="alert" style={{ color: HP.crit, fontSize: TYPE.label }}>
          승인 결정을 처리하지 못했습니다. {approvalDecisionError}
        </span>
      )}

      {(prUrl || commitUrl || (approvalDecision === "granted" && onOpenDeploySurface)) && (
        <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", paddingTop: 10, borderTop: `1px dashed ${UI.line}` }}>
          {prUrl && (
            <a className="product-focusable" href={prUrl} rel="noopener noreferrer" target="_blank" style={manifestLinkStyle}>
              Pull Request 열기 <ExternalLink size={13} />
            </a>
          )}
          {commitUrl && (
            <a className="product-focusable" href={commitUrl} rel="noopener noreferrer" target="_blank" style={manifestLinkStyle}>
              기준 커밋 보기 <ExternalLink size={13} />
            </a>
          )}
          {approvalDecision === "granted" && onOpenDeploySurface && (
            <button type="button" className="product-focusable" onClick={onOpenDeploySurface} style={manifestTextButtonStyle}>
              배포 현황에서 추적
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function manifestWorkflowRecord({
  route,
  approval,
  approvalDecision,
  applyReceipt,
  applyStatus,
  prUrl,
  prFailed,
}: {
  route: "safe-pr" | "direct";
  approval: ResourceManifestApproveEndpoint | null;
  approvalDecision: "granted" | "rejected" | null;
  applyReceipt: ResourceManifestApplyEndpoint | null;
  applyStatus: CommandStatus | null;
  prUrl: string | null;
  prFailed: boolean;
}): string {
  if (prFailed) return "PR 생성 실패";
  if (prUrl) return "PR 생성 완료";
  if (approvalDecision === "rejected") return "변경 거부";
  if (route === "safe-pr") {
    if (approvalDecision === "granted") return "승인 완료 · PR 생성 대기";
    if (approval) return "Safe PR 요청 접수";
  }
  if (applyStatus) return commandStatusLabel(applyStatus.status);
  if (applyReceipt) return "적용 명령 접수";
  return "Git 변경 기록";
}

function githubCommitUrl(repositoryRef: string, sha: string | null): string | null {
  if (!sha) return null;
  const normalized = repositoryRef.trim()
    .replace(/^https:\/\/github\.com\//u, "")
    .replace(/^git@github\.com:/u, "")
    .replace(/\.git$/u, "")
    .replace(/^\/+|\/+$/gu, "");
  return /^[^/\s]+\/[^/\s]+$/u.test(normalized)
    ? `https://github.com/${normalized}/commit/${encodeURIComponent(sha)}`
    : null;
}

const manifestLinkStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  color: BLUE,
  fontSize: TYPE.label,
  fontWeight: 600,
  textDecoration: "none",
};

const manifestTextButtonStyle: React.CSSProperties = {
  border: 0,
  padding: 0,
  background: "transparent",
  color: BLUE,
  fontSize: TYPE.label,
  fontWeight: 600,
  cursor: "pointer",
};

function commandStatusLabel(status: CommandStatus["status"]): string {
  if (status === "queued") return "대기";
  if (status === "leased") return "에이전트 수신";
  if (status === "running") return "적용 중";
  if (status === "completed") return "명령 완료";
  return "실패";
}

function LiveManifestPanel({ source, action }: {
  source: ResourceManifestSourceEndpoint;
  action?: React.ReactNode;
}) {
  return (
    <section aria-label="Live YAML 읽기 전용" style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <b style={{ color: UI.ink, fontSize: TYPE.body }}>Live YAML · 읽기 전용</b>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
          {source.live_observed_at && <span style={{ color: UI.ink3, fontVariantNumeric: "tabular-nums", fontSize: TYPE.caption }}>관측 {source.live_observed_at}</span>}
          {action}
        </div>
      </div>
      {source.live_yaml ? (
        <YamlCodeView value={source.live_yaml} ariaLabel="Live YAML" maxHeight={null} />
      ) : (
        <ManifestNotice tone="warn" title="Live YAML 관측 불가">{source.live_reason ?? "현재 inventory snapshot에 원문이 없습니다."}</ManifestNotice>
      )}
    </section>
  );
}

function ManifestLoadingState({ label, wide }: { label: string; wide: boolean }) {
  return (
    <div
      aria-busy="true"
      aria-label={label}
      role="status"
      style={{
        width: "100%",
        minHeight: wide ? "clamp(360px, 58vh, 620px)" : "clamp(280px, 52vh, 520px)",
        display: "grid",
        placeItems: "center",
      }}
    >
      <Spinner className="size-7" decorative style={{ color: BLUE }} />
    </div>
  );
}

function manifestDraftKey(resourceId: string): string {
  return `opsia:resource-manifest-draft:${resourceId}`;
}

function restoreManifestDraft(resourceId: string, source: ResourceManifestSourceEndpoint): string {
  if (!source.content || !source.source_sha256 || !source.selected) return source.content ?? "";
  try {
    const raw = window.localStorage.getItem(manifestDraftKey(resourceId));
    if (!raw) return source.content;
    const draft = JSON.parse(raw) as {
      applicationId?: unknown;
      sourceSha256?: unknown;
      yaml?: unknown;
    };
    return draft.applicationId === source.selected.application_id
      && draft.sourceSha256 === source.source_sha256
      && typeof draft.yaml === "string"
      ? draft.yaml
      : source.content;
  } catch {
    return source.content;
  }
}

function ManifestNotice({ title, tone = "neutral", children }: { title: string; tone?: "neutral" | "ok" | "warn" | "error"; children: React.ReactNode }) {
  const palette = tone === "ok" ? TINT.ok : tone === "warn" ? TINT.warn : tone === "error" ? TINT.crit : TINT.blue;
  return <div role={tone === "error" ? "alert" : "status"} style={{ border: `1px solid ${palette.bd}`, background: palette.bg, borderRadius: 10, padding: "10px 12px", color: UI.ink2, fontSize: TYPE.label, lineHeight: 1.5 }}>
    <b style={{ display: "block", color: palette.fg, marginBottom: 2 }}>{title}</b>{children}
  </div>;
}

function Pill({ tone = "neutral", children }: { tone?: "neutral" | "ok" | "warn"; children: React.ReactNode }) {
  const color = tone === "ok" ? HP.ok : tone === "warn" ? HP.warn : UI.ink2;
  return <span style={{ border: `1px solid ${tone === "neutral" ? UI.line : `${color}55`}`, background: tone === "neutral" ? inkA(0.035) : `${color}12`, borderRadius: 999, padding: "3px 8px", color, fontSize: TYPE.caption, fontWeight: 600 }}>{children}</span>;
}

function ActionButton({ primary = false, disabled, onClick, children }: { primary?: boolean; disabled: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" className={`product-focusable ${primary ? "product-action" : "product-control"}`} disabled={disabled} onClick={onClick} style={{ border: primary ? "none" : `1px solid ${UI.line}`, background: primary ? BLUE : UI.card, color: primary ? UI.card : BLUE, borderRadius: 9, padding: "7px 12px", fontSize: TYPE.label, fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer" }}>{children}</button>;
}

const selectStyle: React.CSSProperties = { width: "100%", border: `1px solid ${UI.line}`, borderRadius: 9, padding: "8px 10px", background: UI.card, color: UI.ink, fontSize: TYPE.body };
