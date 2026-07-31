import type { ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";

export type ReactRootFactory = (container: HTMLElement) => Root;

export interface ReactRootRegistry {
  container: HTMLElement | null;
  root: Root | null;
}

interface ReactRootRegistryHost {
  __opsiaReactRootRegistry__?: ReactRootRegistry;
  __opsiaMountedReactRoots__?: WeakSet<Root>;
}

function browserRegistry(): ReactRootRegistry {
  const host = globalThis as typeof globalThis & ReactRootRegistryHost;
  host.__opsiaReactRootRegistry__ ??= { container: null, root: null };
  return host.__opsiaReactRootRegistry__;
}

function browserMountedRoots(): WeakSet<Root> {
  const host = globalThis as typeof globalThis & ReactRootRegistryHost;
  host.__opsiaMountedReactRoots__ ??= new WeakSet<Root>();
  return host.__opsiaMountedReactRoots__;
}

/**
 * Reuses the single product root when Vite re-evaluates the entry module.
 * React owns the container until the document replaces it with a new node.
 */
export function acquireReactRoot(
  container: HTMLElement,
  registry: ReactRootRegistry = browserRegistry(),
  rootFactory: ReactRootFactory = createRoot,
): Root {
  if (registry.container === container && registry.root !== null) return registry.root;

  registry.root?.unmount();
  const root = rootFactory(container);
  registry.container = container;
  registry.root = root;
  return root;
}

/**
 * Bootstraps a retained root once. Component modules continue to update through
 * React Fast Refresh without an entry-module re-render that would reset state.
 */
export function renderReactRootOnce(
  root: Root,
  children: ReactNode,
  mountedRoots: WeakSet<Root> = browserMountedRoots(),
): boolean {
  if (mountedRoots.has(root)) return false;

  root.render(children);
  mountedRoots.add(root);
  return true;
}
