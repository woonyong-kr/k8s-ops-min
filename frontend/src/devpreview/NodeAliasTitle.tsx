import { useState, type ReactNode } from "react";
import { Check, Pencil, X } from "lucide-react";

import { NODE_ALIAS_MAX_LENGTH } from "../api/node-aliases-schemas";
import { BLUE, MONO, TYPE, UI, inkA } from "./theme";
import type { NodeAliasView } from "./nodeAliasesFeed";

interface NodeAliasTitleProps {
  nodeName: string;
  alias: NodeAliasView | null;
  onOpen?: () => void;
  onSave?: (nodeName: string, alias: string) => Promise<NodeAliasView | null>;
  onDelete?: (nodeName: string) => Promise<void>;
}

export function NodeAliasTitle({
  nodeName,
  alias,
  onOpen,
  onSave,
  onDelete,
}: NodeAliasTitleProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(alias?.alias ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const displayName = alias?.alias || nodeName;
  const hasAlias = Boolean(alias?.alias && alias.alias !== nodeName);
  const editable = Boolean(onSave && onDelete);

  if (editing) {
    return (
      <form
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault();
          event.stopPropagation();
          void saveDraft();
        }}
        style={{ display: "grid", gap: 5, minWidth: 0 }}
      >
        <span style={{ display: "flex", alignItems: "center", gap: 5, minWidth: 0 }}>
          <input
            aria-label="노드 별칭"
            autoFocus
            disabled={saving}
            maxLength={NODE_ALIAS_MAX_LENGTH}
            onChange={(event) => {
              setDraft(event.target.value);
              setError(null);
            }}
            value={draft}
            style={{
              minWidth: 0,
              flex: 1,
              border: `1px solid ${error ? "#FF5F55" : UI.line}`,
              borderRadius: 7,
              background: UI.card,
              color: UI.ink,
              fontFamily: MONO,
              fontSize: TYPE.label,
              fontWeight: 600,
              outline: "none",
              padding: "5px 7px",
            }}
          />
          <IconButton
            disabled={saving}
            label="별칭 저장"
            onClick={() => undefined}
            type="submit"
          >
            <Check size={13} />
          </IconButton>
          <IconButton
            disabled={saving}
            label="별칭 수정 취소"
            onClick={() => {
              setDraft(alias?.alias ?? "");
              setError(null);
              setEditing(false);
            }}
            type="button"
          >
            <X size={13} />
          </IconButton>
        </span>
        <span style={{ fontSize: TYPE.caption, color: error ? "#C43028" : UI.ink3, lineHeight: 1.35 }}>
          {error ?? nodeName}
        </span>
      </form>
    );
  }

  // 연필 버튼은 제목 "줄 안"에 인라인으로 둔다 — 두 줄 블록 옆에 flex-start로
  // 붙이면 버튼(24px)이 제목 줄(≈20px)과 어긋나 떠 보인다.
  return (
    <span style={{ display: "block", maxWidth: "100%", minWidth: 0 }}>
      <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
        {onOpen ? (
          <button
            type="button"
            aria-label={`${displayName} 노드 파드 보기`}
            className="product-focusable"
            onClick={(event) => {
              event.stopPropagation();
              onOpen();
            }}
            title={hasAlias ? `${displayName} · ${nodeName}` : nodeName}
            style={{
              minWidth: 0,
              flex: "0 1 auto",
              border: "none",
              borderRadius: 5,
              background: "transparent",
              padding: 0,
              textAlign: "left",
              cursor: "pointer",
              fontSize: TYPE.body,
              fontWeight: 600,
              letterSpacing: 0,
              color: UI.ink,
              fontFamily: MONO,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              lineHeight: 1.35,
            }}
          >
            {displayName}
          </button>
        ) : (
          <span
            title={hasAlias ? `${displayName} · ${nodeName}` : nodeName}
            style={{
              minWidth: 0,
              flex: "0 1 auto",
              fontSize: TYPE.body,
              fontWeight: 600,
              letterSpacing: 0,
              color: UI.ink,
              fontFamily: MONO,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              lineHeight: 1.35,
            }}
          >
            {displayName}
          </span>
        )}
        {editable && (
          <IconButton
            label="노드 별칭 수정"
            onClick={() => {
              setDraft(alias?.alias ?? "");
              setError(null);
              setEditing(true);
            }}
            type="button"
          >
            <Pencil size={12} />
          </IconButton>
        )}
      </span>
      {hasAlias && (
        <span
          title={nodeName}
          style={{
            display: "block",
            fontSize: TYPE.caption,
            color: UI.ink3,
            fontFamily: MONO,
            marginTop: 2,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {nodeName}
        </span>
      )}
    </span>
  );

  async function saveDraft() {
    if (!onSave || !onDelete) return;
    const normalized = draft.trim().replace(/\s+/gu, " ");
    setSaving(true);
    setError(null);
    try {
      if (normalized) {
        await onSave(nodeName, normalized);
      } else {
        await onDelete(nodeName);
      }
      setEditing(false);
    } catch {
      setError("별칭을 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }
}

function IconButton({
  children,
  disabled = false,
  label,
  onClick,
  type,
}: {
  children: ReactNode;
  disabled?: boolean;
  label: string;
  onClick: () => void;
  type: "button" | "submit";
}) {
  return (
    <button
      aria-label={label}
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      title={label}
      type={type}
      style={{
        width: 22,
        height: 22,
        border: `1px solid ${UI.line}`,
        borderRadius: 7,
        background: UI.card,
        color: disabled ? UI.ink3 : BLUE,
        cursor: disabled ? "default" : "pointer",
        display: "grid",
        flexShrink: 0,
        placeItems: "center",
        boxShadow: `0 1px 2px ${inkA(0.04)}`,
      }}
    >
      {children}
    </button>
  );
}
