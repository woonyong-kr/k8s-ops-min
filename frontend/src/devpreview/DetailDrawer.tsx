import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from "react";
import { motion, useReducedMotion } from "motion/react";

import {
  SIDE_PANEL_DEFAULT_WIDTH,
  SIDE_PANEL_ENTER_TRANSITION,
  SIDE_PANEL_EXIT_TRANSITION,
  SIDE_PANEL_MIN_WIDTH,
  SIDE_PANEL_SURFACE_STYLE,
  SIDE_PANEL_WIDTH_TRANSITION,
  SidePanelResizeHandle,
  SidePanelWindowControls,
  clampSidePanelWidth,
  sidePanelWidthFromKeyboard,
} from "./SidePanelShell";
import { BLUE, DUR, PRESENT_SCALE, TYPE, UI, inkA } from "./theme";

export type DetailDrawerTab<T extends string> = {
  id: T;
  label: string;
  disabled?: boolean;
  title?: string;
};

export function DetailDrawerTabs<T extends string>({
  active,
  indicatorId,
  items,
  onChange,
}: {
  active: T;
  indicatorId: string;
  items: readonly DetailDrawerTab<T>[];
  onChange: (tab: T) => void;
}) {
  return (
    <div role="tablist" style={{ display: "flex", gap: 2, marginTop: 14 }}>
      {items.map((item) => {
        const selected = active === item.id;
        return (
          <button
            key={item.id}
            className="product-focusable product-control"
            type="button"
            role="tab"
            aria-selected={selected}
            aria-disabled={item.disabled || undefined}
            disabled={item.disabled}
            title={item.title}
            onClick={() => {
              if (!item.disabled) onChange(item.id);
            }}
            style={{
              position: "relative",
              border: "none",
              background: "transparent",
              cursor: item.disabled ? "not-allowed" : "pointer",
              opacity: item.disabled ? 0.52 : 1,
              padding: "8px 12px 10px",
              fontSize: TYPE.body,
              fontWeight: selected ? 600 : 500,
              color: item.disabled ? UI.ink3 : selected ? UI.ink : UI.ink3,
            }}
          >
            {item.label}
            {selected && (
              <motion.span
                aria-hidden="true"
                layoutId={indicatorId}
                style={{
                  position: "absolute",
                  left: 8,
                  right: 8,
                  bottom: 0,
                  height: 2,
                  borderRadius: 2,
                  background: BLUE,
                }}
              />
            )}
          </button>
        );
      })}
    </div>
  );
}

export function DetailDrawer({
  actions,
  ariaLabel,
  bodyRef,
  bodyStyle,
  children,
  expanded,
  forceExpanded = false,
  forceExpandedLabel = "AI 대화 중에는 전체 화면 유지",
  header,
  leftInset = 0,
  navigation,
  onClose,
  onExpandedChange,
  rightInset = 0,
  topInset = 0,
  viewportWidth,
}: {
  actions?: ReactNode;
  ariaLabel: string;
  bodyRef?: RefObject<HTMLDivElement | null>;
  bodyStyle?: CSSProperties;
  children: ReactNode;
  expanded: boolean;
  forceExpanded?: boolean;
  forceExpandedLabel?: string;
  header: ReactNode;
  leftInset?: number;
  navigation?: ReactNode;
  onClose: () => void;
  onExpandedChange: (expanded: boolean) => void;
  rightInset?: number;
  topInset?: number;
  viewportWidth?: number;
}) {
  const reduceMotion = useReducedMotion();
  const drawerRef = useRef<HTMLElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(
    typeof document !== "undefined" && document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  );
  const closeRef = useRef(onClose);
  const dragCleanupRef = useRef<() => void>(() => undefined);
  const [width, setWidth] = useState(SIDE_PANEL_DEFAULT_WIDTH);
  const [dragging, setDragging] = useState(false);
  const full = expanded || forceExpanded;
  const measuredViewportWidth =
    viewportWidth
    ?? (typeof document !== "undefined"
      ? document.documentElement.clientWidth / PRESENT_SCALE
      : SIDE_PANEL_DEFAULT_WIDTH);
  const availableWidth = Math.max(
    0,
    measuredViewportWidth - leftInset - rightInset,
  );
  const renderedWidth = full ? availableWidth : Math.min(width, availableWidth);

  useEffect(() => {
    const root = document.documentElement;
    const body = document.body;
    const previousRootOverflow = root.style.overflow;
    const previousRootOverscroll = root.style.overscrollBehavior;
    const previousBodyOverflow = body.style.overflow;
    const previousBodyOverscroll = body.style.overscrollBehavior;
    root.style.overflow = "hidden";
    root.style.overscrollBehavior = "none";
    body.style.overflow = "hidden";
    body.style.overscrollBehavior = "none";
    return () => {
      root.style.overflow = previousRootOverflow;
      root.style.overscrollBehavior = previousRootOverscroll;
      body.style.overflow = previousBodyOverflow;
      body.style.overscrollBehavior = previousBodyOverscroll;
    };
  }, []);

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previousFocus = previousFocusRef.current;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      closeRef.current();
    };
    document.addEventListener("keydown", onKeyDown);
    drawerRef.current?.focus({ preventScroll: true });
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      window.requestAnimationFrame(() => {
        if (
          previousFocus?.isConnected
          && (document.activeElement === document.body || document.activeElement === null)
        ) {
          previousFocus.focus({ preventScroll: true });
        }
      });
    };
  }, []);

  useEffect(() => () => dragCleanupRef.current(), []);

  const onEdgeDown = (event: React.PointerEvent) => {
    if (full) return;
    event.preventDefault();
    setDragging(true);
    const move = (pointerEvent: PointerEvent) => {
      const cssViewportWidth =
        viewportWidth ?? document.documentElement.clientWidth / PRESENT_SCALE;
      const maxWidth = Math.max(
        0,
        cssViewportWidth - leftInset - rightInset,
      );
      const nextWidth =
        cssViewportWidth - rightInset - pointerEvent.clientX / PRESENT_SCALE;
      setWidth(
        clampSidePanelWidth(nextWidth, SIDE_PANEL_MIN_WIDTH, maxWidth),
      );
    };
    const cleanup = () => {
      setDragging(false);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", cleanup);
      dragCleanupRef.current = () => undefined;
    };
    dragCleanupRef.current();
    dragCleanupRef.current = cleanup;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", cleanup);
  };
  const setClampedWidth = (nextWidth: number) => {
    setWidth(clampSidePanelWidth(
      nextWidth,
      SIDE_PANEL_MIN_WIDTH,
      availableWidth,
    ));
  };
  const onEdgeKeyDown = (event: React.KeyboardEvent) => {
    const nextWidth = sidePanelWidthFromKeyboard({
      currentWidth: width,
      key: event.key,
      maximumWidth: availableWidth,
      minimumWidth: SIDE_PANEL_MIN_WIDTH,
      shiftKey: event.shiftKey,
    });
    if (nextWidth === null) return;
    event.preventDefault();
    setClampedWidth(nextWidth);
  };

  return (
    <>
      <motion.div
        aria-hidden="true"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{
          opacity: 0,
          transition: { duration: reduceMotion ? 0 : DUR.micro },
        }}
        transition={{ duration: reduceMotion ? 0 : DUR.fade }}
        onClick={onClose}
        style={{
          position: "fixed",
          top: topInset,
          left: leftInset,
          right: rightInset,
          bottom: 0,
          background: inkA(0.07),
          zIndex: 70,
        }}
      />
      <motion.aside
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        tabIndex={-1}
        initial={reduceMotion ? false : { x: 40, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        exit={
          reduceMotion
            ? { opacity: 0 }
            : {
                x: 24,
                opacity: 0,
                transition: SIDE_PANEL_EXIT_TRANSITION,
              }
        }
        transition={
          reduceMotion
            ? { duration: 0 }
            : SIDE_PANEL_ENTER_TRANSITION
        }
        style={{
          ...SIDE_PANEL_SURFACE_STYLE,
          position: "fixed",
          top: topInset,
          right: rightInset,
          bottom: 0,
          width: renderedWidth,
          maxWidth: availableWidth,
          boxSizing: "border-box",
          zIndex: 71,
          transition: dragging
            ? "none"
            : SIDE_PANEL_WIDTH_TRANSITION,
        }}
      >
        {!full && (
          <SidePanelResizeHandle
            ariaLabel="상세 패널 폭 조절"
            dragging={dragging}
            minimumWidth={SIDE_PANEL_MIN_WIDTH}
            maximumWidth={availableWidth}
            value={renderedWidth}
            onPointerDown={onEdgeDown}
            onKeyDown={onEdgeKeyDown}
          />
        )}

        <div
          style={{
            flexShrink: 0,
            padding: "16px 20px 0",
            borderBottom: `1px solid ${UI.line}`,
          }}
        >
          <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
            <div style={{ minWidth: 0, flex: 1 }}>{header}</div>
            <SidePanelWindowControls
              actions={actions}
              closeLabel="상세 패널 닫기"
              expanded={full}
              expandedDisabled={forceExpanded}
              expandedDisabledLabel={forceExpandedLabel}
              onClose={onClose}
              onExpandedChange={onExpandedChange}
              panelLabel="상세 패널"
            />
          </div>
          {navigation}
        </div>

        <div
          ref={bodyRef}
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
            overflowX: "hidden",
            overscrollBehavior: "contain",
            scrollbarGutter: "stable",
            padding: "0 20px 28px",
            ...bodyStyle,
          }}
        >
          {children}
        </div>
      </motion.aside>
    </>
  );
}
