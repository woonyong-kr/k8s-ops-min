export interface AiAssistantFilters {
  clusters: string[];
  namespaces: string[];
  applications: string[];
  labels: string[];
  resourceTypes: string[];
  health: string[];
  query: string;
}

export interface AiAssistantContext {
  screen: string;
  filters: AiAssistantFilters;
  selection: { type: "resource"; identity: string } | null;
  time: string | null;
  logStreamId: string | null;
}

export interface AiEvidenceLink {
  type: string;
  id: string;
  label: string;
  link: `/${string}`;
}

export interface AiAlertRuleActionPayload {
  name: string;
  scope: {
    clusters: readonly string[];
    namespaces: readonly string[];
    applications: readonly string[];
    labels: readonly string[];
  };
  metric: "cpu_pct" | "mem_pct" | "restart_count" | "pod_not_ready";
  comparator: ">" | ">=" | "<" | "<=";
  threshold: number;
  forSeconds: number;
  severity: "critical" | "high" | "medium" | "low";
  channels: readonly string[];
  enabled: boolean;
}

export interface AiAlertRuleAction {
  type: "create_alert_rule";
  payload: AiAlertRuleActionPayload;
  rationale: string;
}

export type AiChatActionProposal = AiAlertRuleAction;

export interface AiAssistantAnswer {
  answer: string;
  evidence: AiEvidenceLink[];
  action: AiAlertRuleAction | null;
  answerKind?: "capability" | null;
}

export interface AiAssistantSuggestion {
  id: string;
  label: string;
  prompt: string;
}

export type AiAssistantFailureCode =
  | "unauthorized"
  | "forbidden"
  | "offline"
  | "rate-limited"
  | "unavailable"
  | "invalid-request"
  | "invalid-response"
  | "error";

export class AiAssistantPortFailure extends Error {
  constructor(readonly code: AiAssistantFailureCode) {
    super(`AI assistant failed: ${code}`);
    this.name = "AiAssistantPortFailure";
  }
}

export interface AiAssistantPort {
  ask(
    context: AiAssistantContext,
    message: string,
    signal?: AbortSignal,
  ): Promise<AiAssistantAnswer>;
  loadSuggestions(
    context: AiAssistantContext,
    signal?: AbortSignal,
  ): Promise<AiAssistantSuggestion[]>;
  createAlertRule(
    action: AiAlertRuleAction,
    signal?: AbortSignal,
  ): Promise<{ ruleId: string }>;
}

export const EMPTY_AI_ASSISTANT_PORT: AiAssistantPort = {
  ask: async () => { throw new AiAssistantPortFailure("unavailable"); },
  loadSuggestions: async () => [],
  createAlertRule: async () => { throw new AiAssistantPortFailure("unavailable"); },
};
