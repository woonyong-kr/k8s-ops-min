import {
  type CSSProperties,
  type KeyboardEventHandler,
  type PointerEventHandler,
  type ReactNode,
} from "react";
import { Maximize2, Minimize2, X } from "lucide-react";

import { HP, UI, blueA, inkA } from "./theme";

export const SIDE_PANEL_DEFAULT_WIDTH = 560;
export const SIDE_PANEL_MIN_WIDTH = 460;
export const SIDE_PANEL_KEYBOARD_STEP = 16;
export const SIDE_PANEL_KEYBOARD_LARGE_STEP = 40;

export const SIDE_PANEL_ENTER_TRANSITION = {
  type: "spring",
  bounce: 0.06,
  visualDuration: 0.36,
} as const;

export const SIDE_PANEL_EXIT_TRANSITION = {
  duration: 0.14,
  ease: [0.4, 0, 1, 1],
} as const;

export const SIDE_PANEL_WIDTH_TRANSITION =
  "right .28s cubic-bezier(.32,.72,0,1), width .28s cubic-bezier(.32,.72,0,1), max-width .28s cubic-bezier(.32,.72,0,1)";

export const SIDE_PANEL_SURFACE_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
  background: UI.card,
  borderLeft: `1px solid ${UI.line}`,
  boxShadow: `-24px 0 60px -30px ${inkA(0.3)}`,
  outline: "none",
};

/**
 * Flex child that owns a full-height panel body.
 *
 * `minHeight: 0` is essential here: without it, long panel content contributes
 * its min-content height and can grow past the fixed side-panel viewport.
 */
export const SIDE_PANEL_CONTENT_HOST_STYLE: CSSProperties = {
  display: "flex",
  flex: 1,
  minWidth: 0,
  minHeight: 0,
  overflow: "hidden",
};

export function clampSidePanelWidth(
  width: number,
  minimumWidth: number,
  maximumWidth: number,
): number {
  const safeMaximum = Math.max(0, maximumWidth);
  const safeMinimum = Math.min(Math.max(0, minimumWidth), safeMaximum);
  return Math.min(safeMaximum, Math.max(safeMinimum, width));
}

export function sidePanelWidthFromKeyboard({
  currentWidth,
  key,
  maximumWidth,
  minimumWidth,
  shiftKey = false,
}: {
  currentWidth: number;
  key: string;
  maximumWidth: number;
  minimumWidth: number;
  shiftKey?: boolean;
}): number | null {
  const step = shiftKey
    ? SIDE_PANEL_KEYBOARD_LARGE_STEP
    : SIDE_PANEL_KEYBOARD_STEP;
  if (key === "ArrowLeft") {
    return clampSidePanelWidth(currentWidth + step, minimumWidth, maximumWidth);
  }
  if (key === "ArrowRight") {
    return clampSidePanelWidth(currentWidth - step, minimumWidth, maximumWidth);
  }
  if (key === "Home") {
    return clampSidePanelWidth(maximumWidth, minimumWidth, maximumWidth);
  }
  if (key === "End") {
    return clampSidePanelWidth(minimumWidth, minimumWidth, maximumWidth);
  }
  return null;
}

export function SidePanelIconButton({
  ariaLabel,
  children,
  className,
  disabled = false,
  onClick,
  style,
  title,
  tone = "neutral",
}: {
  ariaLabel: string;
  children: ReactNode;
  className?: string;
  disabled?: boolean;
  onClick?: () => void;
  style?: CSSProperties;
  title?: string;
  tone?: "neutral" | "danger";
}) {
  return (
    <button
      type="button"
      className={`product-focusable product-control${className ? ` ${className}` : ""}`}
      aria-label={ariaLabel}
      title={title}
      disabled={disabled}
      onClick={onClick}
      style={{
        width: 28,
        height: 28,
        padding: 0,
        borderRadius: 999,
        border: "none",
        background: inkA(0.06),
        color: tone === "danger" ? HP.crit : UI.ink2,
        cursor: disabled ? "not-allowed" : "pointer",
        display: "grid",
        placeItems: "center",
        lineHeight: 1,
        transition: "background .15s, color .15s, opacity .15s",
        ...style,
      }}
    >
      {children}
    </button>
  );
}

export function SidePanelWindowControls({
  actions,
  actionsPlacement = "before-toggle",
  closeLabel,
  closeTitle = "닫기",
  expanded,
  expandedDisabled = false,
  expandedDisabledLabel,
  onClose,
  onExpandedChange,
  panelLabel,
  showExpandedControl = true,
}: {
  actions?: ReactNode;
  actionsPlacement?: "before-toggle" | "after-toggle";
  closeLabel: string;
  closeTitle?: string;
  expanded: boolean;
  expandedDisabled?: boolean;
  expandedDisabledLabel?: string;
  onClose: () => void;
  onExpandedChange: (expanded: boolean) => void;
  panelLabel: string;
  showExpandedControl?: boolean;
}) {
  const toggleLabel = expandedDisabled
    ? expandedDisabledLabel ?? `${panelLabel} 크기 변경 불가`
    : expanded
      ? `${panelLabel} 축소`
      : `${panelLabel} 전체 화면`;
  const toggleTitle = expandedDisabled
    ? expandedDisabledLabel ?? `${panelLabel} 크기 변경 불가`
    : expanded
      ? "패널로 축소"
      : "전체 화면";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        flexShrink: 0,
      }}
    >
      {actionsPlacement === "before-toggle" ? actions : null}
      {showExpandedControl ? (
        <SidePanelIconButton
          ariaLabel={toggleLabel}
          title={toggleTitle}
          disabled={expandedDisabled}
          onClick={() => onExpandedChange(!expanded)}
        >
          {expanded ? (
            <Minimize2 size={14} strokeWidth={2.2} />
          ) : (
            <Maximize2 size={14} strokeWidth={2.2} />
          )}
        </SidePanelIconButton>
      ) : null}
      {actionsPlacement === "after-toggle" ? actions : null}
      <SidePanelIconButton
        ariaLabel={closeLabel}
        title={closeTitle}
        onClick={onClose}
      >
        <X size={15} strokeWidth={2.2} />
      </SidePanelIconButton>
    </div>
  );
}

export function SidePanelResizeHandle({
  ariaLabel,
  dragging,
  maximumWidth,
  minimumWidth,
  onKeyDown,
  onPointerDown,
  placement = "overlay",
  value,
}: {
  ariaLabel: string;
  dragging: boolean;
  maximumWidth: number;
  minimumWidth: number;
  onKeyDown: KeyboardEventHandler<HTMLDivElement>;
  onPointerDown: PointerEventHandler<HTMLDivElement>;
  placement?: "overlay" | "inline";
  value: number;
}) {
  const inline = placement === "inline";
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      aria-valuemin={Math.round(Math.min(minimumWidth, maximumWidth))}
      aria-valuemax={Math.round(Math.max(0, maximumWidth))}
      aria-valuenow={Math.round(value)}
      tabIndex={0}
      title="드래그하거나 방향키로 폭 조절"
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      className="product-focusable"
      style={
        inline
          ? {
              position: "relative",
              width: 1,
              flexShrink: 0,
              cursor: "col-resize",
              zIndex: 5,
              background: dragging ? blueA(0.35) : UI.line,
              transition: "background .15s",
            }
          : {
              position: "absolute",
              left: -2,
              top: 0,
              bottom: 0,
              width: 6,
              cursor: "col-resize",
              zIndex: 5,
              background: dragging ? blueA(0.35) : "transparent",
              transition: "background .15s",
            }
      }
    >
      {inline ? (
        <span
          aria-hidden="true"
          style={{
            position: "absolute",
            left: -3,
            top: 0,
            bottom: 0,
            width: 7,
            cursor: "col-resize",
            background: "transparent",
          }}
        />
      ) : null}
    </div>
  );
}
