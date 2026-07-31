export interface AiRecoveryPreviewLine {
  kind: "context" | "add" | "remove";
  content: string;
}

export interface AiRecoveryPreview {
  title: string;
  fileName: string;
  lines: readonly AiRecoveryPreviewLine[];
  note?: string;
}

export interface AiRecoveryExecutionReceipt {
  accepted: boolean;
  eventId: string;
  correlationId: string;
  commandId: string | null;
}

export interface AiRecoveryHandoff {
  id: string;
  correlationId: string;
  prompt: string;
  displayPrompt: string;
  actionTitle: string;
  actionRoute: "auto" | "safe_pr" | "approval_required" | string;
  validationChecks: readonly string[];
  contextView: string;
  contextScope: string;
  preview?: AiRecoveryPreview;
  execute: () => Promise<AiRecoveryExecutionReceipt | null>;
}
