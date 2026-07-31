import { createContext, useContext, useMemo, type ReactNode } from "react";
import type {
  AuthActionIssue,
  AuthenticatedAuthState,
  ProductSession,
} from "./authContract";

export interface AuthSessionGate {
  reportUnauthorized: () => void;
}

const AuthSessionGateContext = createContext<AuthSessionGate | null>(null);

export function AuthSessionGateProvider({
  children,
  reportUnauthorized,
}: {
  children: ReactNode;
  reportUnauthorized: () => void;
}) {
  const value = useMemo<AuthSessionGate>(
    () => ({ reportUnauthorized }),
    [reportUnauthorized],
  );

  return (
    <AuthSessionGateContext.Provider value={value}>
      {children}
    </AuthSessionGateContext.Provider>
  );
}

export function useAuthSessionGate(): AuthSessionGate {
  const gate = useContext(AuthSessionGateContext);
  if (gate === null) {
    throw new Error("useAuthSessionGate must be used within AuthSessionGateProvider");
  }
  return gate;
}

export function AuthenticatedSessionRender({
  onSignOut,
  reportUnauthorized,
  render,
  session,
  signOutIssue,
  signOutPending,
}: {
  onSignOut: () => void;
  reportUnauthorized: () => void;
  render: (auth: AuthenticatedAuthState) => ReactNode;
  session: ProductSession;
  signOutIssue: AuthActionIssue | null;
  signOutPending: boolean;
}) {
  return (
    <AuthSessionGateProvider reportUnauthorized={reportUnauthorized}>
      {render({ session, signOutIssue, signOutPending, onSignOut })}
    </AuthSessionGateProvider>
  );
}
