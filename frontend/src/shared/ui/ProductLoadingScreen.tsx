import type { ReactNode } from "react";
import { DEFAULT_LOCALE, translate, useOptionalI18n } from "../i18n";
import { ProductPageFrame } from "./ProductPageFrame";
import { Skeleton } from "./primitives/skeleton";

type ProductStatePlacement = "root" | "content";

export function ProductLoadingScreen({
  placement,
  preview,
}: {
  placement: ProductStatePlacement | undefined;
  preview: ReactNode;
}) {
  const i18n = useOptionalI18n();
  const t = i18n?.t ?? ((key: Parameters<typeof translate>[1], params?: Parameters<typeof translate>[2]) =>
    translate(DEFAULT_LOCALE, key, params));
  const isContent = placement === "content";
  const label = t(isContent ? "loading.default" : "loading.session");
  const geometry = preview ?? (isContent
    ? <ContentLoadingGeometry />
    : <ProductShellLoadingGeometry />);
  const content = (
    <>
      <span aria-label={label} className="sr-only" role="status">{label}</span>
      <LoadingPreviewSlot>{geometry}</LoadingPreviewSlot>
    </>
  );

  if (isContent) {
    return (
      <section
        aria-busy="true"
        aria-label={label}
        className="min-h-full bg-background text-foreground"
        tabIndex={-1}
      >
        {content}
      </section>
    );
  }

  return (
    <main
      aria-busy="true"
      aria-label={label}
      className="min-h-svh bg-background text-foreground"
      id="product-main"
      tabIndex={-1}
    >
      {content}
    </main>
  );
}

function ContentLoadingGeometry() {
  return (
    <ProductPageFrame>
      <div className="flex min-w-0 justify-end">
        <Skeleton aria-hidden="true" className="size-8 shrink-0" />
      </div>
      <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="grid min-w-0 gap-4">
          <div className="grid overflow-hidden rounded-xl border bg-card">
            <div className="flex items-center justify-between border-b p-4">
              <Skeleton aria-hidden="true" className="h-4 w-28" />
              <Skeleton aria-hidden="true" className="h-4 w-16" />
            </div>
            <div className="grid grid-cols-2 gap-px bg-border lg:grid-cols-4">
              {Array.from({ length: 4 }, (_, index) => (
                <Skeleton
                  aria-hidden="true"
                  className="h-20 rounded-none bg-card"
                  key={index}
                />
              ))}
            </div>
            <div className="grid gap-4 border-t p-4 sm:grid-cols-2">
              <Skeleton aria-hidden="true" className="h-10" />
              <Skeleton aria-hidden="true" className="h-10" />
            </div>
          </div>
          <Skeleton aria-hidden="true" className="h-[22rem] rounded-xl" />
        </div>
        <Skeleton aria-hidden="true" className="h-[30rem] rounded-xl" />
      </div>
    </ProductPageFrame>
  );
}

function ProductShellLoadingGeometry() {
  return (
    <div
      className="grid min-h-svh md:grid-cols-[var(--product-sidebar-width)_minmax(0,1fr)]"
      data-slot="product-shell-loading"
    >
      <aside className="hidden border-r bg-sidebar p-3 md:grid md:grid-rows-[2.5rem_1fr_auto] md:gap-5">
        <div className="flex items-center gap-2">
          <Skeleton aria-hidden="true" className="size-8" />
          <Skeleton aria-hidden="true" className="h-4 w-24" />
        </div>
        <div className="grid content-start gap-2">
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton aria-hidden="true" className="h-9" key={index} />
          ))}
        </div>
        <Skeleton aria-hidden="true" className="h-9" />
      </aside>
      <div className="grid min-w-0 grid-rows-[auto_1fr]">
        <header className="flex min-h-14 flex-wrap items-center gap-2 border-b px-4 py-2 lg:flex-nowrap">
          <div className="order-1 flex min-w-0 items-center gap-2">
            <Skeleton aria-hidden="true" className="size-8 md:hidden" />
          </div>
          <div
            className="order-3 h-8 w-full min-w-0 lg:order-2 lg:flex-1"
            data-slot="loading-cluster-scope"
          >
            <Skeleton
              aria-hidden="true"
              className="size-full max-w-(--product-cluster-select-width)"
            />
          </div>
          <div className="order-2 ml-auto flex items-center gap-1 lg:order-3">
            <div
              className="hidden content-center justify-items-end gap-1 lg:grid lg:w-(--product-toolbar-identity-width)"
              data-slot="loading-session-identity"
            >
              <Skeleton aria-hidden="true" className="h-3 w-full" />
              <Skeleton aria-hidden="true" className="h-3 w-3/4" />
            </div>
            <Skeleton aria-hidden="true" className="size-8" />
            <Skeleton aria-hidden="true" className="size-8" />
            <Skeleton
              aria-hidden="true"
              className="h-7 w-(--product-toolbar-compact-control-width)"
            />
            <Skeleton aria-hidden="true" className="size-8" />
          </div>
        </header>
        <ContentLoadingGeometry />
      </div>
    </div>
  );
}

function LoadingPreviewSlot({ children }: { children: ReactNode }) {
  return (
    <div
      aria-hidden="true"
      className="contents pointer-events-none select-none"
      data-slot="loading-preview"
      inert
    >
      {children}
    </div>
  );
}
