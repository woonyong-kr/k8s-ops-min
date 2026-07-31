// VP-021 — Kyro AI 어시스턴트 대화·파트 계약.
import type { AiChatActionProposal, AiEvidenceLink } from "./aiAssistantContract";

export type AiMessagePart =
  | AiTextPart
  | AiStepsPart
  | AiEvidencePart
  | AiLinksPart
  | AiActionPart
  | AiResultPart
  | AiStatusPart;

export interface AiTextPart {
  kind: "text";
  markdown: string;
}

export interface AiStep {
  id: string;
  label: string;
  detail: string | null;
  state: "running" | "done" | "failed";
}
export interface AiStepsPart {
  kind: "steps";
  steps: AiStep[];
  running: boolean;
}

export interface AiEvidencePart {
  kind: "evidence";
  items: AiEvidenceLink[];
}

export type AiPageLinkIcon = "resources" | "incident" | "gitops" | "cluster" | "alert";
export interface AiPageLink {
  label: string;
  href: `/${string}`;
  icon?: AiPageLinkIcon;
}
export interface AiLinksPart {
  kind: "links";
  items: AiPageLink[];
}

export interface AiActionPart {
  kind: "action";
  proposal: AiChatActionProposal;
}

export type AiTone = "healthy" | "warning" | "critical" | "neutral";
export interface AiResultMetric {
  label: string;
  value: string;
  tone: AiTone;
}
export interface AiResultPart {
  kind: "result";
  title: string;
  tone: AiTone;
  summary: string;
  metrics?: AiResultMetric[];
}

export interface AiStatusPart {
  kind: "status";
  state: "pending" | "failed";
  reason?: string;
}

export interface AiTurn {
  id: string;
  role: "user" | "assistant";
  question?: string;
  parts?: AiMessagePart[];
  createdAt: string;
  collapsed: boolean;
  summary?: string;
  /** 서버가 알림 액션을 완성하려고 되물은 턴 — 클라이언트가 보류 중인
      요청 문장을 누적해 다음 전송에 합치는 신호로 쓴다. */
  clarification?: boolean;
}

export interface AiConversationSummary {
  id: string;
  title: string;
  updatedAt: string;
}

export interface AiConversation {
  id: string;
  title: string;
  updatedAt: string;
  turns: AiTurn[];
}
