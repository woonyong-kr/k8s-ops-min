// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type {
  ResourceManifestPreviewEndpoint,
  ResourceManifestSourceEndpoint,
} from "../api/resource-manifests-schemas";

const manifestApi = vi.hoisted(() => ({
  apply: vi.fn(),
  approve: vi.fn(),
  getSource: vi.fn(),
  preview: vi.fn(),
}));

vi.mock("./resourceManifestFeed", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./resourceManifestFeed")>()),
  applyResourceManifestEdit: manifestApi.apply,
  approveResourceManifestEdit: manifestApi.approve,
  getResourceManifestSource: manifestApi.getSource,
  previewResourceManifestEdit: manifestApi.preview,
}));

vi.mock("../api/approvals", () => ({
  grantApproval: vi.fn(),
  rejectApproval: vi.fn(),
}));

import { LiveResourceManifestEditor } from "./resourceManifestEditor";

const ORIGINAL_YAML = [
  "apiVersion: apps/v1",
  "kind: Deployment",
  "metadata:",
  "  name: api-server",
  "spec:",
  "  replicas: 2",
  "",
].join("\n");

const EDITED_YAML = ORIGINAL_YAML.replace("replicas: 2", "replicas: 1");

function source(revision: number): ResourceManifestSourceEndpoint {
  return {
    resource_id: "resource-1",
    status: "available",
    choices: [],
    selected: {
      application_id: "application-1",
      application_name: "demo-game",
      repository_ref: "example/demo-game",
      branch: "main",
      manifest_path: "k8s/api-server.yaml",
      environment: "target",
    },
    base_sha: `base-${revision}`,
    source_sha256: `source-${revision}`,
    source_revision_token: `revision-${revision}`,
    content: revision === 1 ? ORIGINAL_YAML : ORIGINAL_YAML.replace("replicas: 2", "replicas: 3"),
    reason: null,
    live_yaml: ORIGINAL_YAML,
    live_observed_at: "2026-07-24T00:00:00Z",
    live_reason: null,
    edit_target: {
      resource_id: "resource-1",
      relationship: "self",
      kind: "Deployment",
      namespace: "sandbox",
      name: "api-server",
    },
  };
}

const VALID_PREVIEW: ResourceManifestPreviewEndpoint = {
  valid: true,
  changed: true,
  base_sha: "base-1",
  source_sha256: "source-1",
  desired_sha256: "desired-1",
  diff: "- replicas: 2\n+ replicas: 1",
  errors: [],
  warnings: [],
  apply_availability: "available",
  apply_reason_codes: [],
  impact: [{
    api_version: "apps/v1",
    kind: "Deployment",
    namespace: "sandbox",
    name: "api-server",
    selected: true,
  }],
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

beforeEach(() => {
  for (const mock of Object.values(manifestApi)) mock.mockReset();
  window.localStorage.clear();
});

describe("LiveResourceManifestEditor stale source recovery", () => {
  it("refreshes a stale preview authority once, preserves YAML, and waits for a manual preview", async () => {
    manifestApi.getSource
      .mockResolvedValueOnce(source(1))
      .mockResolvedValueOnce(source(2));
    manifestApi.preview
      .mockRejectedValueOnce(sourceConflict("manifest_source_stale"))
      .mockResolvedValueOnce({ ...VALID_PREVIEW, base_sha: "base-2", source_sha256: "source-2" });

    render(<LiveResourceManifestEditor resourceId="resource-1" />);

    expect(await screen.findByRole("region", { name: "Live YAML 읽기 전용" })).not.toBeNull();
    expect(screen.queryByRole("textbox", { name: "Git YAML 원본 편집기" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "편집" }));
    const editor = await screen.findByRole("textbox", { name: "Git YAML 원본 편집기" });
    fireEvent.change(editor, { target: { value: EDITED_YAML } });
    fireEvent.click(screen.getByRole("button", { name: "변경 검증·미리보기" }));

    await screen.findByText("Git 원본 변경 감지");
    await waitFor(() => expect(manifestApi.getSource).toHaveBeenCalledTimes(2));

    expect((screen.getByRole("textbox", { name: "Git YAML 원본 편집기" }) as HTMLTextAreaElement).value)
      .toBe(EDITED_YAML);
    expect(manifestApi.preview).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("서버 검증 통과")).toBeNull();
    expect(screen.queryByRole("button", { name: "Safe PR 요청" })).toBeNull();
    expect(screen.getByText(/이전 미리보기와 확인은 무효화했습니다/u)).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "변경 검증·미리보기" }));

    await waitFor(() => expect(manifestApi.preview).toHaveBeenCalledTimes(2));
    expect(manifestApi.preview.mock.calls[1]?.[1]).toMatchObject({
      applicationId: "application-1",
      baseSha: "base-2",
      sourceSha256: "source-2",
      sourceRevisionToken: "revision-2",
      editedYaml: EDITED_YAML,
    });
  });

  it("invalidates a Safe PR confirmation and never re-approves after a revision conflict", async () => {
    manifestApi.getSource
      .mockResolvedValueOnce(source(1))
      .mockResolvedValueOnce(source(2));
    manifestApi.preview.mockResolvedValueOnce({
      ...VALID_PREVIEW,
      apply_availability: "unavailable",
    });
    manifestApi.approve.mockRejectedValueOnce(
      sourceConflict("manifest_source_revision_invalid"),
    );

    render(<LiveResourceManifestEditor resourceId="resource-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "편집" }));
    const editor = await screen.findByRole("textbox", { name: "Git YAML 원본 편집기" });
    fireEvent.change(editor, { target: { value: EDITED_YAML } });
    fireEvent.click(screen.getByRole("button", { name: "변경 검증·미리보기" }));
    await screen.findByText("서버 검증 통과");
    fireEvent.change(screen.getByRole("textbox", { name: "변경 사유" }), {
      target: { value: "로비 용량 조정" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Safe PR 요청" }));

    await screen.findByText("Git 원본 변경 감지");
    await waitFor(() => expect(manifestApi.getSource).toHaveBeenCalledTimes(2));

    expect(manifestApi.approve).toHaveBeenCalledTimes(1);
    expect(manifestApi.preview).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Safe PR 요청" })).toBeNull();
    expect(screen.queryByText("Safe PR 요청 접수")).toBeNull();
    expect((screen.getByRole("textbox", { name: "Git YAML 원본 편집기" }) as HTMLTextAreaElement).value)
      .toBe(EDITED_YAML);
  });
});

describe("LiveResourceManifestEditor read-only entry", () => {
  it("shows only a centered spinner while the YAML source is loading", () => {
    manifestApi.getSource.mockReturnValue(new Promise(() => undefined));

    const { container } = render(<LiveResourceManifestEditor resourceId="resource-1" wide />);

    expect(screen.getByRole("status", { name: "YAML 소스 확인 중" })).not.toBeNull();
    expect(container.querySelector('[data-slot="spinner"]')).not.toBeNull();
    expect(screen.queryByText("YAML 소스 확인 중")).toBeNull();
    expect(screen.queryByText(/Git에 고정된 실제 매니페스트/u)).toBeNull();
  });

  it("does not flash the loading notice when the YAML source resolves quickly", async () => {
    manifestApi.getSource.mockResolvedValue(source(1));

    render(<LiveResourceManifestEditor resourceId="resource-1" />);

    expect(screen.queryByText("YAML 소스 확인 중")).toBeNull();
    expect(await screen.findByRole("region", { name: "Live YAML 읽기 전용" })).not.toBeNull();
    expect(screen.queryByText("YAML 소스 확인 중")).toBeNull();
  });

  it("shows the same spinner-only state while resolving resource identity", () => {
    const { container } = render(
      <LiveResourceManifestEditor resourceId="" resolving />,
    );

    expect(screen.getByRole("status", { name: "YAML 정체성 확인 중" })).not.toBeNull();
    expect(container.querySelector('[data-slot="spinner"]')).not.toBeNull();
    expect(screen.queryByText("YAML 정체성 확인 중")).toBeNull();
    expect(screen.queryByText(/inventory key/u)).toBeNull();
  });

  it("shows only the editor after edit and restores a saved draft", async () => {
    manifestApi.getSource.mockResolvedValue(source(1));
    const first = render(<LiveResourceManifestEditor resourceId="resource-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "편집" }));
    const editor = await screen.findByRole("textbox", { name: "Git YAML 원본 편집기" });
    expect(editor.getAttribute("wrap")).toBe("off");
    expect(editor.classList.contains("manifest-yaml-editor")).toBe(true);
    expect((editor as HTMLElement).style.overflowX).toBe("scroll");
    expect(screen.queryByRole("region", { name: "Live YAML 읽기 전용" })).toBeNull();
    fireEvent.change(editor, { target: { value: EDITED_YAML } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(screen.getByText("임시 저장됨")).not.toBeNull();

    first.unmount();
    render(<LiveResourceManifestEditor resourceId="resource-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "편집" }));

    expect((await screen.findByRole("textbox", { name: "Git YAML 원본 편집기" }) as HTMLTextAreaElement).value)
      .toBe(EDITED_YAML);
  });

  it("reuses a preloaded source when the side-by-side editor opens", async () => {
    render(
      <LiveResourceManifestEditor
        resourceId="resource-1"
        mode="edit"
        initialSource={source(1)}
      />,
    );

    expect(await screen.findByRole("textbox", { name: "Git YAML 원본 편집기" })).not.toBeNull();
    expect(manifestApi.getSource).not.toHaveBeenCalled();
    expect(screen.queryByText("YAML 소스 확인 중")).toBeNull();
  });

  it("opens the editor and reports editability only after the edit button is clicked", async () => {
    manifestApi.getSource.mockResolvedValueOnce(source(1));
    const onEditableChange = vi.fn();

    render(
      <LiveResourceManifestEditor
        resourceId="resource-1"
        onEditableChange={onEditableChange}
      />,
    );

    expect(await screen.findByRole("region", { name: "Live YAML 읽기 전용" })).not.toBeNull();
    expect(screen.queryByRole("textbox", { name: "Git YAML 원본 편집기" })).toBeNull();
    expect(onEditableChange).toHaveBeenLastCalledWith(false);

    fireEvent.click(screen.getByRole("button", { name: "편집" }));

    expect(await screen.findByRole("textbox", { name: "Git YAML 원본 편집기" })).not.toBeNull();
    expect(onEditableChange).toHaveBeenLastCalledWith(true);
  });
});

function sourceConflict(
  code: "manifest_source_stale" | "manifest_source_revision_invalid",
): ApiError {
  return new ApiError("http", "The Git source changed after it was loaded.", {
    status: 409,
    code,
    detail: "The Git source changed after it was loaded.",
  });
}
