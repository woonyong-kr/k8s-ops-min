import { OpsiaRoot } from "./app/OpsiaRoot";
import { acquireReactRoot, renderReactRootOnce } from "./app/reactRootMount";

const deployedSourceSha = import.meta.env.VITE_SOURCE_SHA;
if (/^[0-9a-f]{40}$/.test(deployedSourceSha ?? "")) {
  document.documentElement.dataset.sourceSha = deployedSourceSha;
}

// UI-PHASE2-001 §5.1: the unified shell is the single root product app. It is
// mounted here from the normal root entry (no StrictMode double-invoke, matching
// the shell's timer/animation visual contract). The superseded Phase-1 legacy
// root graph has been removed (§5.5); this is the only production root.
const rootContainer = document.getElementById("root");
if (!(rootContainer instanceof HTMLElement)) {
  throw new Error("Kyro root container is missing.");
}

// Bootstrap the retained root once; HMR must preserve the active surface and tab.
renderReactRootOnce(acquireReactRoot(rootContainer), <OpsiaRoot />);

// Keep the entry module inside Vite's HMR graph. Without this boundary Vite
// reloads the document for every main.tsx edit, which discards all UI state.
if (import.meta.hot) {
  import.meta.hot.accept();
}
