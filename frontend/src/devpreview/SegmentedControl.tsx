import { useRef, type KeyboardEvent, type ReactNode } from "react";
import { motion } from "motion/react";

import { SOFT, TYPE, UI, inkA } from "./theme";

export interface SegmentedControlItem<T extends string> {
  value: T;
  label: ReactNode;
  disabled?: boolean;
}

export function SegmentedControl<T extends string>({
  active,
  ariaLabel,
  indicatorId,
  items,
  onChange,
}: {
  active: T;
  ariaLabel: string;
  indicatorId: string;
  items: readonly SegmentedControlItem<T>[];
  onChange: (value: T) => void;
}) {
  const buttonRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const moveFocus = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();

    const enabled = items
      .map((item, itemIndex) => ({ item, itemIndex }))
      .filter(({ item }) => !item.disabled);
    if (enabled.length === 0) return;

    const current = enabled.findIndex(({ itemIndex }) => itemIndex === index);
    const next = event.key === "Home"
      ? enabled[0]
      : event.key === "End"
        ? enabled[enabled.length - 1]
        : enabled[
            (current + (event.key === "ArrowRight" ? 1 : -1) + enabled.length)
              % enabled.length
          ];
    buttonRefs.current[next.itemIndex]?.focus();
    onChange(next.item.value);
  };

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      style={{ display: "flex", gap: 2, background: inkA(0.05), borderRadius: 9, padding: 2 }}
    >
      {items.map((item, index) => {
        const selected = active === item.value;
        return (
          <button
            key={item.value}
            ref={(element) => {
              buttonRefs.current[index] = element;
            }}
            type="button"
            role="tab"
            className="product-focusable product-control"
            aria-selected={selected}
            disabled={item.disabled}
            tabIndex={selected ? 0 : -1}
            onClick={() => {
              if (!item.disabled) onChange(item.value);
            }}
            onKeyDown={(event) => moveFocus(event, index)}
            style={{
              position: "relative",
              border: "none",
              background: "transparent",
              borderRadius: 7,
              padding: "5px 16px",
              fontSize: TYPE.label,
              fontWeight: 600,
              color: selected ? UI.ink : UI.ink3,
              cursor: item.disabled ? "not-allowed" : "pointer",
              opacity: item.disabled ? 0.52 : 1,
            }}
          >
            {selected && (
              <motion.span
                aria-hidden="true"
                layoutId={indicatorId}
                transition={SOFT}
                style={{
                  position: "absolute",
                  inset: 0,
                  background: UI.card,
                  borderRadius: 7,
                  boxShadow: `0 1px 4px ${inkA(0.14)}`,
                }}
              />
            )}
            <span style={{ position: "relative" }}>{item.label}</span>
          </button>
        );
      })}
    </div>
  );
}
