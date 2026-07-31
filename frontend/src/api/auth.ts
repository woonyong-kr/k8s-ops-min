import { apiRequest } from "./client";
import {
  authSessionSchema,
  logoutResponseSchema,
  type AuthSession,
} from "./schemas";

export interface LoginCredentials {
  email: string;
  password: string;
}

export function getSession(signal?: AbortSignal): Promise<AuthSession> {
  return apiRequest("/api/auth/session", authSessionSchema, { signal });
}

export function login(
  credentials: LoginCredentials,
  signal?: AbortSignal,
): Promise<AuthSession> {
  return apiRequest("/api/auth/login", authSessionSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(credentials),
    signal,
  });
}

export async function logout(signal?: AbortSignal): Promise<void> {
  await apiRequest("/api/auth/logout", logoutResponseSchema, {
    method: "POST",
    signal,
  });
}
