import {
  CircleAlert,
  FileSearch,
  Inbox,
  LockKeyhole,
  ShieldCheck,
  WifiOff,
} from "lucide-react";
import { useEffect, useId, useRef, type MouseEvent, type ReactNode } from "react";
import { Alert, AlertDescription, AlertTitle } from "./primitives/alert";
import { Button } from "./primitives/button";
import {
  DEFAULT_LOCALE,
  translate,
  useOptionalI18n,
  type MessageKey,
  type TranslationFunction,
} from "../i18n";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
} from "./primitives/empty";
import { Spinner } from "./primitives/spinner";
import { ProductLoadingScreen } from "./ProductLoadingScreen";

export type ProductStateKind =
  | "loading"
  | "empty"
  | "not-found"
  | "forbidden"
  | "offline"
  | "error"
  | "release";

export type ProductStateErrorCode =
  | "network"
  | "forbidden"
  | "invalid-response"
  | "server"
  | "unknown";

export interface ProductStateIssue {
  code: ProductStateErrorCode;
  safeDetail?: string;
  correlationId?: string;
}

export interface ProductStateRetry {
  label?: string;
  pending: boolean;
  onRetry: () => void;
}

type ProductStatePlacement = "root" | "content";

interface StateBase {
  headingLevel?: 1 | 2;
  placement?: ProductStatePlacement;
}

export type ProductStateScreenProps =
  | (StateBase & {
      kind: "loading";
      issue?: never;
      retry?: never;
      loadingPreview?: ReactNode;
    })
  | (StateBase & { kind: "empty"; issue?: never; retry?: never })
  | (StateBase & { kind: "not-found"; issue?: never; retry?: never })
  | (StateBase & {
      kind: "offline";
      issue: ProductStateIssue & { code: "network" };
      retry?: ProductStateRetry;
    })
  | (StateBase & {
      kind: "error";
      issue: ProductStateIssue & {
        code: "invalid-response" | "server" | "unknown";
      };
      retry?: ProductStateRetry;
    })
  | (StateBase & {
      kind: "forbidden";
      issue: ProductStateIssue & { code: "forbidden" };
      retry?: never;
    })
  | (StateBase & {
      action?: ReactNode;
      kind: "release";
      placement?: "root";
      issue?: never;
      retry?: never;
    });

const stateCopy: Record<Exclude<ProductStateKind, "loading">, {
  titleKey: MessageKey;
  bodyKey: MessageKey;
}> = {
  empty: {
    titleKey: "state.empty.title",
    bodyKey: "state.empty.body",
  },
  "not-found": {
    titleKey: "state.notFound.title",
    bodyKey: "state.notFound.body",
  },
  forbidden: {
    titleKey: "state.forbidden.title",
    bodyKey: "state.forbidden.body",
  },
  offline: {
    titleKey: "state.offline.title",
    bodyKey: "state.offline.body",
  },
  error: {
    titleKey: "state.error.title",
    bodyKey: "state.error.body",
  },
  release: {
    titleKey: "state.release.title",
    bodyKey: "state.release.body",
  },
};

export function ProductStateScreen(props: ProductStateScreenProps) {
  const titleId = useId();
  const i18n = useOptionalI18n();
  const t = i18n?.t ?? fallbackTranslate;
  if (props.kind === "loading") {
    return (
      <ProductLoadingScreen
        placement={props.placement}
        preview={props.loadingPreview}
      />
    );
  }
  const { kind } = props;
  const isContent = props.placement === "content";
  const headingLevel = props.headingLevel ?? (isContent ? 2 : 1);
  const copy = stateCopy[kind];
  const issue = "issue" in props ? props.issue : undefined;
  const retry = "retry" in props ? props.retry : undefined;
  const action = "action" in props ? props.action : undefined;
  const issueKind = isIssueStateKind(kind) ? kind : null;
  const content = (
    <Empty className="w-full max-w-lg items-start rounded-xl border border-solid bg-card p-8 text-left text-card-foreground shadow-sm">
      <EmptyMedia variant="icon">
        <StateIcon kind={kind} />
      </EmptyMedia>
      <EmptyHeader className="max-w-none items-start text-left">
        <StateHeading level={headingLevel} titleId={titleId}>{t(copy.titleKey)}</StateHeading>
        <EmptyDescription className="text-pretty leading-6">{t(copy.bodyKey)}</EmptyDescription>
      </EmptyHeader>
      {issue && issueKind ? <IssueAlert issue={issue} kind={issueKind} t={t} /> : null}
      {retry ? <RetryAction retry={retry} t={t} /> : null}
      {action ? <EmptyContent className="mt-2 max-w-none items-stretch">{action}</EmptyContent> : null}
    </Empty>
  );

  if (props.placement === "content") {
    return (
      <section
        aria-labelledby={titleId}
        className="grid min-h-full place-items-center bg-background p-6 text-foreground"
        tabIndex={-1}
      >
        {content}
      </section>
    );
  }

  return (
    <main
      aria-labelledby={titleId}
      className="grid min-h-svh place-items-center bg-background p-6 text-foreground"
      id="product-main"
      tabIndex={-1}
    >
      {content}
    </main>
  );
}

function StateIcon({ kind }: { kind: Exclude<ProductStateKind, "loading"> }) {
  const iconByKind: Record<Exclude<ProductStateKind, "loading">, ReactNode> = {
    empty: <Inbox aria-hidden="true" />,
    "not-found": <FileSearch aria-hidden="true" />,
    forbidden: <LockKeyhole aria-hidden="true" />,
    offline: <WifiOff aria-hidden="true" />,
    error: <CircleAlert aria-hidden="true" />,
    release: <ShieldCheck aria-hidden="true" />,
  };
  return iconByKind[kind];
}

function StateHeading({
  children,
  level,
  titleId,
}: {
  children: ReactNode;
  level: 1 | 2;
  titleId: string;
}) {
  const className = "text-balance text-2xl font-semibold tracking-tight";
  return level === 1
    ? <h1 className={className} id={titleId}>{children}</h1>
    : <h2 className={className} id={titleId}>{children}</h2>;
}

function isIssueStateKind(kind: ProductStateKind): kind is "forbidden" | "offline" | "error" {
  return kind === "forbidden" || kind === "offline" || kind === "error";
}

function IssueAlert({
  issue,
  kind,
  t,
}: {
  issue: ProductStateIssue;
  kind: "forbidden" | "offline" | "error";
  t: TranslationFunction;
}) {
  const titleKey = kind === "forbidden"
    ? "state.issue.permissionInfo"
    : kind === "offline"
      ? "state.issue.connectionError"
      : "state.issue.responseError";

  return (
    <Alert className="mt-2" variant={kind === "error" ? "destructive" : "default"}>
      <CircleAlert aria-hidden="true" />
      <AlertTitle>{t(titleKey)}</AlertTitle>
      <AlertDescription>
        <p className="flex flex-wrap gap-x-2">
          <span>{t("state.issue.code")}</span>
          <code className="font-mono text-xs">{issue.code}</code>
        </p>
        {issue.safeDetail ? <p className="break-words [overflow-wrap:anywhere]">{issue.safeDetail}</p> : null}
        {issue.correlationId ? (
          <p className="flex flex-wrap gap-x-2">
            <span>{t("state.issue.correlationId")}</span>
            <code className="break-all font-mono text-xs">{issue.correlationId}</code>
          </p>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

function RetryAction({ retry, t }: { retry: ProductStateRetry; t: TranslationFunction }) {
  const invokedRef = useRef(false);
  const pendingRef = useRef(retry.pending);
  const label = retry.label?.trim() || t("common.action.retry");

  useEffect(() => {
    pendingRef.current = retry.pending;
    if (!retry.pending) invokedRef.current = false;
  }, [retry.pending]);

  const handleRetry = (event: MouseEvent<HTMLButtonElement>) => {
    if (event.detail > 1 || retry.pending || invokedRef.current) return;
    invokedRef.current = true;
    try {
      retry.onRetry();
    } finally {
      window.setTimeout(() => {
        if (!pendingRef.current) invokedRef.current = false;
      });
    }
  };

  return (
    <EmptyContent className="mt-2 items-start">
      <Button
        aria-busy={retry.pending || undefined}
        className="h-auto min-h-8 max-w-full whitespace-normal text-left [overflow-wrap:anywhere]"
        disabled={retry.pending}
        onClick={handleRetry}
      >
        {retry.pending ? <Spinner data-icon="inline-start" decorative /> : null}
        {retry.pending ? t("common.action.inProgress", { action: label }) : label}
      </Button>
    </EmptyContent>
  );
}

const fallbackTranslate: TranslationFunction = (key, params) =>
  translate(DEFAULT_LOCALE, key, params);
