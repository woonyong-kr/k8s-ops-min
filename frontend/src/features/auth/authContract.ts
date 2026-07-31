import type { MessageKey, TranslationParameters } from "../../shared/i18n";

export interface ProductSession {
  displayName?: string | null;
  email?: string | null;
  userId: string;
  roles: readonly string[];
  workspaceId: string;
}

export interface AuthCredentials {
  email: string;
  password: string;
}

export type AuthSessionResult =
  | { status: "authenticated"; session: ProductSession }
  | { status: "unauthenticated" };

export type AuthFailureCode =
  | "invalid-credentials"
  | "email-unverified"
  | "approval-pending"
  | "forbidden"
  | "rate-limited"
  | "network"
  | "invalid-response"
  | "server";

export class AuthPortFailure extends Error {
  readonly code: AuthFailureCode;
  readonly retryAfterSeconds: number | null;
  readonly safeDetail: string | null;

  constructor(
    code: AuthFailureCode,
    retryAfterSeconds: number | null = null,
    safeDetail: string | null = null,
  ) {
    super(`Authentication port failed: ${code}`);
    this.name = "AuthPortFailure";
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
    this.safeDetail = safeDetail;
  }
}

export interface AuthPort {
  loadSession(signal?: AbortSignal): Promise<AuthSessionResult>;
  signIn(credentials: AuthCredentials, signal?: AbortSignal): Promise<ProductSession>;
  signOut(signal?: AbortSignal): Promise<void>;
}

export interface AuthActionIssue {
  code: AuthFailureCode;
  messageKey: MessageKey;
  messageParams?: TranslationParameters;
  retryAfterSeconds: number | null;
  safeDetail?: string;
}

export interface AuthenticatedAuthState {
  session: ProductSession;
  signOutIssue: AuthActionIssue | null;
  signOutPending: boolean;
  onSignOut: () => void;
}
