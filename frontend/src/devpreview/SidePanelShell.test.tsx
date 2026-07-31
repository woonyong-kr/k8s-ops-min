// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  SIDE_PANEL_KEYBOARD_LARGE_STEP,
  SIDE_PANEL_KEYBOARD_STEP,
  SIDE_PANEL_CONTENT_HOST_STYLE,
  SidePanelResizeHandle,
  SidePanelWindowControls,
  clampSidePanelWidth,
  sidePanelWidthFromKeyboard,
} from "./SidePanelShell";

afterEach(cleanup);

describe("SidePanelWindowControls", () => {
  it("uses one expand and close contract while preserving panel actions", () => {
    const onClose = vi.fn();
    const onExpandedChange = vi.fn();

    render(
      <SidePanelWindowControls
        actions={<button type="button">보조 작업</button>}
        closeLabel="상세 패널 닫기"
        expanded={false}
        onClose={onClose}
        onExpandedChange={onExpandedChange}
        panelLabel="상세 패널"
      />,
    );

    expect(screen.getByRole("button", { name: "보조 작업" })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "상세 패널 전체 화면" }));
    expect(onExpandedChange).toHaveBeenCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: "상세 패널 닫기" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("disables resizing with the supplied operational reason", () => {
    const onExpandedChange = vi.fn();

    render(
      <SidePanelWindowControls
        closeLabel="AI 패널 닫기"
        expanded
        expandedDisabled
        expandedDisabledLabel="AI 대화 중에는 전체 화면 유지"
        onClose={() => undefined}
        onExpandedChange={onExpandedChange}
        panelLabel="AI 패널"
      />,
    );

    const button = screen.getByRole("button", {
      name: "AI 대화 중에는 전체 화면 유지",
    });
    expect(button.getAttribute("disabled")).not.toBeNull();
    fireEvent.click(button);
    expect(onExpandedChange).not.toHaveBeenCalled();
  });

  it("can reuse the same chrome for a fixed-width panel without a size toggle", () => {
    render(
      <SidePanelWindowControls
        closeLabel="AI 패널 닫기"
        expanded={false}
        onClose={() => undefined}
        onExpandedChange={() => undefined}
        panelLabel="AI 패널"
        showExpandedControl={false}
      />,
    );

    expect(screen.queryByRole("button", { name: "AI 패널 전체 화면" })).toBeNull();
    expect(screen.getByRole("button", { name: "AI 패널 닫기" })).not.toBeNull();
  });
});

describe("SidePanelResizeHandle", () => {
  it("exposes its width range and delegates pointer and keyboard input", () => {
    const onPointerDown = vi.fn();
    const onKeyDown = vi.fn();

    render(
      <SidePanelResizeHandle
        ariaLabel="AI 패널 폭 조절"
        dragging={false}
        maximumWidth={760}
        minimumWidth={380}
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        placement="inline"
        value={440}
      />,
    );

    const separator = screen.getByRole("separator", { name: "AI 패널 폭 조절" });
    expect(separator.getAttribute("aria-valuemin")).toBe("380");
    expect(separator.getAttribute("aria-valuemax")).toBe("760");
    expect(separator.getAttribute("aria-valuenow")).toBe("440");

    fireEvent.pointerDown(separator, { clientX: 900 });
    fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect(onPointerDown).toHaveBeenCalledTimes(1);
    expect(onKeyDown).toHaveBeenCalledTimes(1);
  });
});

describe("side panel width math", () => {
  it("clamps narrow viewports without inverting the range", () => {
    expect(clampSidePanelWidth(560, 460, 420)).toBe(420);
    expect(clampSidePanelWidth(-10, 460, 420)).toBe(420);
  });

  it("uses the shared keyboard steps and terminal widths", () => {
    expect(sidePanelWidthFromKeyboard({
      currentWidth: 440,
      key: "ArrowLeft",
      maximumWidth: 760,
      minimumWidth: 380,
    })).toBe(440 + SIDE_PANEL_KEYBOARD_STEP);

    expect(sidePanelWidthFromKeyboard({
      currentWidth: 440,
      key: "ArrowRight",
      maximumWidth: 760,
      minimumWidth: 380,
      shiftKey: true,
    })).toBe(440 - SIDE_PANEL_KEYBOARD_LARGE_STEP);

    expect(sidePanelWidthFromKeyboard({
      currentWidth: 440,
      key: "Home",
      maximumWidth: 760,
      minimumWidth: 380,
    })).toBe(760);

    expect(sidePanelWidthFromKeyboard({
      currentWidth: 440,
      key: "End",
      maximumWidth: 760,
      minimumWidth: 380,
    })).toBe(380);

    expect(sidePanelWidthFromKeyboard({
      currentWidth: 440,
      key: "Escape",
      maximumWidth: 760,
      minimumWidth: 380,
    })).toBeNull();
  });
});

describe("side panel content sizing", () => {
  it("allows long panel content to shrink inside a fixed viewport", () => {
    expect(SIDE_PANEL_CONTENT_HOST_STYLE).toMatchObject({
      display: "flex",
      flex: 1,
      minHeight: 0,
      minWidth: 0,
      overflow: "hidden",
    });
  });
});
