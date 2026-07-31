import type {
  CSSProperties,
  HTMLAttributes,
  ReactNode,
} from "react";

import {
  BLUE,
  MONO,
  PRESENT_SCALE,
  RADIUS,
  RESOURCE_LAYOUT,
  TYPE,
  UI,
  blueA,
  inkA,
} from "./theme";

export function resourceAuxiliaryViewportHeight(
  viewportTopInset: number,
  stickyTop: number,
  scaled = true,
): string {
  const viewport = scaled ? `100vh / ${PRESENT_SCALE}` : "100vh";
  return `calc(${viewport} - ${viewportTopInset + stickyTop + RESOURCE_LAYOUT.viewportBottomGap}px)`;
}

interface ResourceAuxiliaryPanelProps extends Omit<HTMLAttributes<HTMLElement>, "children"> {
  header?: ReactNode;
  children: ReactNode;
  bodyStyle?: CSSProperties;
}

/**
 * 리소스 관점이 공통으로 쓰는 우측 보조 패널.
 * 외곽, 고정 헤더, 단일 스크롤 본문의 책임을 이 컴포넌트 한 곳에 둔다.
 */
export function ResourceAuxiliaryPanel({
  header,
  children,
  bodyStyle,
  style,
  ...props
}: ResourceAuxiliaryPanelProps) {
  return (
    <aside
      data-resource-aux-panel="true"
      {...props}
      style={{
        width: RESOURCE_LAYOUT.auxiliaryWidth,
        boxSizing: "border-box",
        flexShrink: 0,
        alignSelf: "flex-start",
        background: UI.card,
        border: `1px solid ${UI.line}`,
        borderRadius: RADIUS.panel,
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        ...style,
      }}
    >
      {header != null && (
        <div
          data-resource-aux-header="true"
          style={{
            minHeight: RESOURCE_LAYOUT.auxiliaryHeaderHeight,
            flex: `0 0 ${RESOURCE_LAYOUT.auxiliaryHeaderHeight}px`,
            boxSizing: "border-box",
            display: "flex",
            alignItems: "center",
            padding: RESOURCE_LAYOUT.auxiliaryHeaderPadding,
            borderBottom: `1px solid ${UI.line2}`,
            background: UI.card,
          }}
        >
          {header}
        </div>
      )}
      <div
        data-resource-aux-body="true"
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          overflowX: "hidden",
          overscrollBehavior: "contain",
          scrollbarGutter: "stable",
          padding: RESOURCE_LAYOUT.auxiliaryBodyPadding,
          ...bodyStyle,
        }}
      >
        {children}
      </div>
    </aside>
  );
}

export function ResourceAuxiliaryHeader({
  title,
  value,
  detail,
  action,
}: {
  title: ReactNode;
  value?: ReactNode;
  detail?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div style={{ width: "100%", minWidth: 0, display: "flex", alignItems: "center", gap: 6 }}>
      <strong style={{ minWidth: 0, color: UI.heading, fontSize: TYPE.body, fontWeight: 700, letterSpacing: "-0.02em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {title}
      </strong>
      {value != null && (
        <span style={{ flexShrink: 0, color: UI.ink3, fontFamily: MONO, fontSize: TYPE.caption, fontVariantNumeric: "tabular-nums" }}>
          {value}
        </span>
      )}
      {detail != null && (
        <span style={{ minWidth: 0, color: UI.ink3, fontSize: TYPE.caption, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {detail}
        </span>
      )}
      {action != null && <span style={{ marginLeft: "auto", flexShrink: 0 }}>{action}</span>}
    </div>
  );
}

export function ResourceAuxiliarySection({
  label,
  value,
  children,
}: {
  label: ReactNode;
  value?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section style={{ minWidth: 0 }}>
      <div
        style={{
          minHeight: RESOURCE_LAYOUT.auxiliarySectionHeight,
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 8px 4px",
          color: UI.ink3,
          fontSize: TYPE.caption,
          fontWeight: 650,
          letterSpacing: "0.04em",
        }}
      >
        <span>{label}</span>
        {value != null && (
          <span style={{ marginLeft: "auto", fontFamily: MONO, fontVariantNumeric: "tabular-nums", letterSpacing: 0 }}>
            {value}
          </span>
        )}
      </div>
      <div style={{ display: "grid", gap: RESOURCE_LAYOUT.auxiliaryRowGap }}>{children}</div>
    </section>
  );
}

interface ResourceAuxiliaryRowProps extends Omit<HTMLAttributes<HTMLDivElement>, "title" | "onClick"> {
  title: ReactNode;
  tooltip?: string;
  meta?: ReactNode;
  icon: ReactNode;
  trailing?: ReactNode;
  secondaryAction?: ReactNode;
  selected?: boolean;
  titleFontFamily?: CSSProperties["fontFamily"];
  onActivate?: () => void;
  onDoubleActivate?: () => void;
  ariaLabel?: string;
}

/**
 * 아이콘 / 본문 / 우측 값의 열을 모든 보조 패널에서 동일하게 맞춘다.
 * 별도 액션(예: 즐겨찾기)은 전체 행 클릭 레이어 위에 안전하게 올린다.
 */
export function ResourceAuxiliaryRow({
  title,
  tooltip,
  meta,
  icon,
  trailing,
  secondaryAction,
  selected = false,
  titleFontFamily,
  onActivate,
  onDoubleActivate,
  ariaLabel,
  style,
  className,
  ...props
}: ResourceAuxiliaryRowProps) {
  const interactive = Boolean(onActivate);

  return (
    <div
      {...props}
      className={`resource-aux-row${className ? ` ${className}` : ""}`}
      role={interactive ? undefined : props.role}
      tabIndex={interactive ? undefined : props.tabIndex}
      aria-label={interactive ? undefined : props["aria-label"]}
      aria-selected={interactive ? undefined : selected || undefined}
      onClick={undefined}
      onDoubleClick={interactive ? undefined : props.onDoubleClick}
      onKeyDown={interactive ? undefined : props.onKeyDown}
      title={tooltip}
      style={{
        position: "relative",
        width: "100%",
        minWidth: 0,
        minHeight: RESOURCE_LAYOUT.auxiliaryRowHeight,
        boxSizing: "border-box",
        display: "grid",
        gridTemplateColumns: `${RESOURCE_LAYOUT.auxiliaryIconColumn}px minmax(0, 1fr) auto`,
        alignItems: "center",
        columnGap: 8,
        padding: RESOURCE_LAYOUT.auxiliaryRowPadding,
        border: `1px solid ${selected ? blueA(0.32) : "transparent"}`,
        borderRadius: RADIUS.control,
        background: selected ? blueA(0.09) : "transparent",
        color: UI.ink,
        textAlign: "left",
        cursor: interactive ? "pointer" : "default",
        outline: "none",
        ...style,
      }}
    >
      {interactive && (
        <button
          type="button"
          className="product-focusable product-control"
          aria-label={ariaLabel}
          aria-selected={selected || undefined}
          onClick={onActivate}
          onDoubleClick={onDoubleActivate}
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 1,
            width: "100%",
            height: "100%",
            border: 0,
            borderRadius: RADIUS.control,
            background: "transparent",
            cursor: "pointer",
          }}
        />
      )}
      <span aria-hidden="true" style={{ position: "relative", zIndex: 2, pointerEvents: "none", width: RESOURCE_LAYOUT.auxiliaryIconColumn, height: RESOURCE_LAYOUT.auxiliaryIconColumn, display: "grid", placeItems: "center", flexShrink: 0 }}>
        {icon}
      </span>
      <span style={{ position: "relative", zIndex: 2, pointerEvents: "none", minWidth: 0, alignSelf: "center" }}>
        <strong
          data-pod-highlight-primary
          style={{
            display: "block",
            minWidth: 0,
            color: selected ? BLUE : UI.ink,
            fontFamily: titleFontFamily,
            fontSize: TYPE.label,
            fontWeight: selected ? 700 : 650,
            lineHeight: 1.3,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {title}
        </strong>
        {meta != null && (
          <span
            style={{
              display: "block",
              minWidth: 0,
              marginTop: 2,
              color: UI.ink3,
              fontSize: TYPE.caption,
              lineHeight: 1.25,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {meta}
          </span>
        )}
      </span>
      <span style={{ position: "relative", zIndex: 2, pointerEvents: secondaryAction ? "auto" : "none", minWidth: RESOURCE_LAYOUT.auxiliaryTrailingColumn, display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 4, color: selected ? BLUE : UI.ink3, fontFamily: MONO, fontSize: TYPE.caption, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
        <span style={{ pointerEvents: "none", display: "flex", alignItems: "center" }}>{trailing}</span>
        {secondaryAction && <span style={{ pointerEvents: "auto", display: "flex", alignItems: "center" }}>{secondaryAction}</span>}
      </span>
    </div>
  );
}

export const resourceAuxiliaryFooterButtonStyle: CSSProperties = {
  width: "100%",
  minHeight: 40,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 7,
  marginTop: 8,
  padding: "8px 10px",
  border: `1px solid ${UI.line2}`,
  borderRadius: RADIUS.control,
  background: UI.card,
  color: UI.ink3,
  fontSize: TYPE.caption,
  fontWeight: 600,
  cursor: "pointer",
  boxShadow: `0 6px 14px -14px ${inkA(0.3)}`,
};
