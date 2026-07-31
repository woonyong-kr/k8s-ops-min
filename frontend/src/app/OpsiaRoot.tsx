import { lazy, Suspense } from "react";

// Keep this component outside the entry module. When Vite re-evaluates main.tsx,
// React receives the same component boundary and preserves the mounted app state.
const UnifiedApp = lazy(() => (
  import("../devpreview-unified").then((module) => ({ default: module.UnifiedApp }))
));

export function OpsiaRoot() {
  return (
    <Suspense fallback={null}>
      <UnifiedApp />
    </Suspense>
  );
}
