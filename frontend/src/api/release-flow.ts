import { apiRequest, type ApiPath } from "./client";
import { connectApplication, listApplications } from "./applications";
import { listClusters } from "./clusters";
import {
  releaseGeneratedManifestSchema,
  releasePlanListSchema,
  releasePlanResponseSchema,
  releasePreviewResponseSchema,
  releaseReadinessSchema,
  releaseRunListSchema,
  releaseRunResponseSchema,
  releaseSafePrSchema,
  type ReleasePlanApi,
} from "./release-flow-schemas";
import { encodePathSegment, withQuery } from "./url";

export type ReleaseRunAction =
  | "advance"
  | "pause"
  | "resume"
  | "retry"
  | "rollback"
  | "cancel"
  | "notify";

export function createReleaseFlowClient() {
  return {
    listApplications: (signal?: AbortSignal) => listApplications({ signal }),
    listClusters: (signal?: AbortSignal) => listClusters({}, signal),
    connectApplication,
    listPlans: (signal?: AbortSignal) =>
      apiRequest("/api/release-plans", releasePlanListSchema, { signal }),
    listRuns: (planId?: string, signal?: AbortSignal) => {
      const path = withQuery("/api/release-runs" as ApiPath, [["plan_id", planId]]);
      return apiRequest(path, releaseRunListSchema, { signal });
    },
    savePlan: async (plan: ReleasePlanApi, signal?: AbortSignal) => {
      const path = plan.plan_id
        ? `/api/release-plans/${encodePathSegment(plan.plan_id)}` as ApiPath
        : "/api/release-plans" as ApiPath;
      const response = await apiRequest(path, releasePlanResponseSchema, jsonRequest(
        plan.plan_id ? "PUT" : "POST",
        releasePlanPayload(plan),
        signal,
      ));
      return response.plan;
    },
    previewPlan: async (plan: ReleasePlanApi, signal?: AbortSignal) => {
      const response = await apiRequest(
        "/api/release-plans/preview",
        releasePreviewResponseSchema,
        jsonRequest("POST", releasePlanPayload(plan), signal),
      );
      return response.preview;
    },
    checkReadiness: (plan: ReleasePlanApi, signal?: AbortSignal) =>
      apiRequest(
        "/api/release-readiness",
        releaseReadinessSchema,
        jsonRequest("POST", releasePlanPayload(plan), signal),
      ),
    startPlan: async (plan: ReleasePlanApi, signal?: AbortSignal) => {
      const response = await apiRequest(
        "/api/release-plans/start",
        releaseRunResponseSchema,
        jsonRequest("POST", releasePlanPayload(plan), signal),
      );
      return response.run;
    },
    renderManifest: (plan: ReleasePlanApi, stepIndex: number, signal?: AbortSignal) =>
      apiRequest(
        "/api/release-plans/render-manifest",
        releaseGeneratedManifestSchema,
        jsonRequest("POST", { plan: releasePlanPayload(plan), step_index: stepIndex }, signal),
      ),
    submitSafePr: (plan: ReleasePlanApi, stepIndex: number, signal?: AbortSignal) =>
      apiRequest(
        "/api/release-plans/render-manifest/safe-pr",
        releaseSafePrSchema,
        jsonRequest("POST", { plan: releasePlanPayload(plan), step_index: stepIndex }, signal),
      ),
    runAction: async (
      runId: string,
      action: ReleaseRunAction,
      reason?: string,
      signal?: AbortSignal,
    ) => {
      const path = `/api/release-runs/${encodePathSegment(runId)}/${action}` as ApiPath;
      const response = await apiRequest(
        path,
        releaseRunResponseSchema,
        jsonRequest("POST", { reason }, signal),
      );
      return response.run;
    },
  };
}

function jsonRequest(method: string, body: unknown, signal?: AbortSignal): RequestInit {
  return {
    method,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  };
}

function releasePlanPayload(plan: ReleasePlanApi) {
  return {
    name: plan.name,
    description: plan.description,
    status: plan.status,
    settings: plan.settings,
    steps: plan.steps.map((step, position) => ({
      application_id: step.application_id,
      name: step.name,
      position,
      depends_on: step.depends_on,
      config: step.config,
    })),
  };
}
