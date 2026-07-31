import { useCallback, useEffect, useRef, useState } from "react";

import {
  getBrowserRefreshPolicies,
  getSettingsAccessProfile,
  getUiPreferences,
  updateUiPreferences,
} from "../api/settings";
import type {
  BrowserRefreshPoliciesResponse,
  SettingsAccessProfileResponse,
  UiPreferences,
} from "../api/settings-schemas";

// UI-PHASE2-001 §3 Settings: typed live adapters (apiBoundary) for the /settings
// surface. Preferences (theme/locale) are read from `GET /api/settings` and
// written back through `PUT /api/settings` with optimistic concurrency
// (expected_revision) and CSRF (applied by the api layer). The access profile
// (`GET /api/settings/access`) and auto-refresh cadences
// (`GET /api/refresh-policies`) are real reads — never a fabricated
// "connected/미지원" placeholder. A save only fires on an explicit user click and
// rolls the optimistic value back on failure.

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

// ── UI preferences (read + optimistic write) ──
export type SettingsStatus = "loading" | "ready" | "unavailable";

export interface UiPreferencesView {
  status: SettingsStatus;
  workspaceId: string | null;
  userId: string | null;
  theme: UiPreferences["theme"];
  locale: UiPreferences["locale"];
  revision: number | null;
  updatedAt: string | null;
  saving: boolean;
  saveError: boolean;
  /** Persists a preference change; only invoked from an explicit user action. */
  save: (next: Partial<UiPreferences>) => void;
}

type UiPreferencesState = Omit<UiPreferencesView, "save">;

const INITIAL_PREFERENCES: UiPreferencesState = {
  status: "loading",
  workspaceId: null,
  userId: null,
  theme: "system",
  locale: "en",
  revision: null,
  updatedAt: null,
  saving: false,
  saveError: false,
};

export function useUiPreferences(): UiPreferencesView {
  const [state, setState] = useState<UiPreferencesState>(INITIAL_PREFERENCES);
  const stateRef = useRef(state);
  // Mirror the latest committed state into the ref outside render so `save`
  // (an event handler) can read the current revision without a stale closure.
  useEffect(() => {
    stateRef.current = state;
  });

  useEffect(() => {
    const controller = new AbortController();
    void getUiPreferences(controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        setState((prev) => ({
          ...prev,
          status: "ready",
          workspaceId: response.workspace_id,
          userId: response.user_id,
          theme: response.preferences.theme,
          locale: response.preferences.locale,
          revision: response.revision,
          updatedAt: response.updated_at,
        }));
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setState((prev) => ({ ...prev, status: "unavailable" }));
      });
    return () => controller.abort();
  }, []);

  const save = useCallback((next: Partial<UiPreferences>) => {
    const current = stateRef.current;
    if (current.status !== "ready" || current.revision === null || current.saving) return;
    const merged: UiPreferences = {
      theme: next.theme ?? current.theme,
      locale: next.locale ?? current.locale,
    };
    if (merged.theme === current.theme && merged.locale === current.locale) return;
    const rollback = { theme: current.theme, locale: current.locale, revision: current.revision };
    // Optimistic apply.
    setState((prev) => ({ ...prev, theme: merged.theme, locale: merged.locale, saving: true, saveError: false }));
    void updateUiPreferences({ preferences: merged, expectedRevision: rollback.revision })
      .then((response) => {
        setState((prev) => ({
          ...prev,
          status: "ready",
          workspaceId: response.workspace_id,
          userId: response.user_id,
          theme: response.preferences.theme,
          locale: response.preferences.locale,
          revision: response.revision,
          updatedAt: response.updated_at,
          saving: false,
          saveError: false,
        }));
      })
      .catch((cause: unknown) => {
        if (isAbortError(cause)) return;
        // Roll the optimistic value back to the server-authoritative snapshot.
        setState((prev) => ({
          ...prev,
          theme: rollback.theme,
          locale: rollback.locale,
          revision: rollback.revision,
          saving: false,
          saveError: true,
        }));
      });
  }, []);

  return { ...state, save };
}

// ── Browser refresh policies (read-only cadence inventory) ──
export interface RefreshPolicyView {
  key: string;
  refreshAfterSeconds: number;
  staleAfterSeconds: number | null;
  eventInvalidation: boolean;
}

export interface RefreshPoliciesView {
  status: SettingsStatus;
  revision: string | null;
  items: RefreshPolicyView[];
}

function toRefreshPolicies(response: BrowserRefreshPoliciesResponse): RefreshPolicyView[] {
  return Object.entries(response.policies)
    .map(([key, policy]) => ({
      key,
      refreshAfterSeconds: policy.refresh_after_seconds,
      staleAfterSeconds: policy.stale_after_seconds,
      eventInvalidation: policy.event_invalidation,
    }))
    .sort((left, right) => left.key.localeCompare(right.key));
}

export function useRefreshPolicies(): RefreshPoliciesView {
  const [view, setView] = useState<RefreshPoliciesView>({ status: "loading", revision: null, items: [] });
  useEffect(() => {
    const controller = new AbortController();
    void getBrowserRefreshPolicies(controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        setView({ status: "ready", revision: response.revision, items: toRefreshPolicies(response) });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setView({ status: "unavailable", revision: null, items: [] });
      });
    return () => controller.abort();
  }, []);
  return view;
}

// ── Access profile (RBAC/permission evidence for the session's first cluster) ──
export interface SettingsAccessView {
  status: SettingsStatus;
  clusterId: string | null;
  roles: string[];
  allowedCount: number;
  permissionCount: number;
  kubernetesRulesObserved: boolean;
  restrictedResourceCount: number | null;
}

const INITIAL_ACCESS: SettingsAccessView = {
  status: "loading",
  clusterId: null,
  roles: [],
  allowedCount: 0,
  permissionCount: 0,
  kubernetesRulesObserved: false,
  restrictedResourceCount: null,
};

function toAccessView(profile: SettingsAccessProfileResponse): SettingsAccessView {
  return {
    status: "ready",
    clusterId: profile.cluster_id,
    roles: [...profile.roles],
    allowedCount: profile.permissions.filter((permission) => permission.allowed).length,
    permissionCount: profile.permissions.length,
    kubernetesRulesObserved: profile.kubernetes_rules.status === "observed",
    restrictedResourceCount: profile.restricted_resource_types.status === "observed"
      ? profile.restricted_resource_types.items.length
      : null,
  };
}

/**
 * Reads the RBAC access profile for the first session-visible cluster. When no
 * cluster is registered yet, this is an honest "ready with no cluster" state,
 * not a fabricated permission set.
 */
export function useSettingsAccess(clusterId: string | null): SettingsAccessView {
  const [view, setView] = useState<SettingsAccessView>(INITIAL_ACCESS);
  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    if (clusterId === null) {
      void Promise.resolve().then(() => {
        if (!signal.aborted) setView({ ...INITIAL_ACCESS, status: "ready" });
      });
      return () => controller.abort();
    }
    void (async () => {
      const profile = await getSettingsAccessProfile({ clusterId }, signal);
      if (signal.aborted) return;
      setView(toAccessView(profile));
    })().catch((cause: unknown) => {
      if (signal.aborted || isAbortError(cause)) return;
      setView({ ...INITIAL_ACCESS, status: "unavailable" });
    });
    return () => controller.abort();
  }, [clusterId]);
  return view;
}
