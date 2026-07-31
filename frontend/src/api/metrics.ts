import {ApiError, apiRequest, type ApiPath} from './client';
import {
  agentDebugQueryReceiptSchema,
  clusterUsageResponseSchema,
  commandStatusSchema,
  prometheusQueryDefinitionSchema,
  telemetryCommandResultSchema,
  type AgentDebugQueryReceipt,
  type ClusterUsageResponse,
  type CommandStatus,
  type PrometheusQueryDefinition,
  type PrometheusRangeResult,
} from './metrics-schemas';
import {encodePathSegment, withQuery} from './url';

export const COMMAND_POLL_INTERVAL_MS = 3_000;
export const COMMAND_POLL_TIMEOUT_MS = 60_000;

const DEFAULT_USAGE_LIMIT = 288;
const MIN_USAGE_LIMIT = 1;
const MAX_USAGE_LIMIT = 2_000;
const TELEMETRY_QUERY_ACTION = 'telemetry.query.run';

export interface ClusterUsageOptions {
  limit?: number;
}

export interface SubmittedPrometheusQuery {
  receipt: AgentDebugQueryReceipt;
  query: PrometheusQueryDefinition;
}

export interface PollCommandOptions {
  signal?: AbortSignal;
}

export type RunPrometheusQueryOptions = PollCommandOptions;

export type MetricCommandSummary = Omit<CommandStatus, 'result'>;

export interface PrometheusQueryRun {
  queryName: string;
  receipt: AgentDebugQueryReceipt;
  command: MetricCommandSummary;
  result: PrometheusRangeResult;
}

export type MetricQueryExecutionErrorKind =
  | 'timeout'
  | 'failed'
  | 'empty-result';

export class MetricQueryExecutionError extends Error {
  readonly kind: MetricQueryExecutionErrorKind;
  readonly command: MetricCommandSummary | null;

  constructor(
    kind: MetricQueryExecutionErrorKind,
    message: string,
    command: MetricCommandSummary | null = null,
  ) {
    super(message);
    this.name = 'MetricQueryExecutionError';
    this.kind = kind;
    this.command = command;
  }
}

/** Loads only the stable usage fields that product state is allowed to consume. */
export function getClusterUsage(
  clusterId: string,
  options: ClusterUsageOptions = {},
  signal?: AbortSignal,
): Promise<ClusterUsageResponse> {
  const limit = options.limit ?? DEFAULT_USAGE_LIMIT;
  assertUsageLimit(limit);
  const basePath =
    `/api/clusters/${encodePathSegment(clusterId)}/usage` as ApiPath;
  const path = withQuery(basePath, [['limit', limit]]);
  return apiRequest(path, clusterUsageResponseSchema, {signal});
}

// 백엔드에 프로메테우스 실행 요청 한번 보내고 command_id를 받는다
/**
 * Queues exactly one Prometheus command. There is deliberately no transport or
 * application retry here: a possibly-sent POST must converge through its
 * receipt and command status rather than create another command.
 */
export async function submitPrometheusQuery(
  clusterId: string,
  query: PrometheusQueryDefinition,
  signal?: AbortSignal,
): Promise<SubmittedPrometheusQuery> {
  const validatedQuery = prometheusQueryDefinitionSchema.parse(query);
  const receipt = await apiRequest(
    '/api/agent/debug/query',
    agentDebugQueryReceiptSchema,
    {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({cluster_id: clusterId, query: validatedQuery}),
      signal,
    },
  );
  return {receipt, query: validatedQuery};
}

// 백엔드에 현재 명령 상태를 물어본다
export function getCommandStatus(
  commandId: string,
  signal?: AbortSignal,
): Promise<CommandStatus> {
  const path = `/api/commands/${encodePathSegment(commandId)}` as ApiPath;
  return apiRequest(path, commandStatusSchema, {signal});
}

/** Polls one receipt without ever re-enqueueing its originating POST. */
export async function pollCommand(
  commandId: string,
  options: PollCommandOptions = {},
): Promise<CommandStatus> {
  const startedAt = Date.now();

  while (true) {
    const latest = await getCommandStatus(commandId, options.signal);
    if (latest.status === 'completed' || latest.status === 'failed') {
      return latest;
    }

    const elapsed = Date.now() - startedAt;
    if (elapsed >= COMMAND_POLL_TIMEOUT_MS) {
      throw new MetricQueryExecutionError(
        'timeout',
        'Metric query did not finish within 60 seconds.',
        commandSummary(latest),
      );
    }

    await waitForNextPoll(
      Math.min(COMMAND_POLL_INTERVAL_MS, COMMAND_POLL_TIMEOUT_MS - elapsed),
      options.signal,
    );
  }
}

export async function runPrometheusQuery(
  clusterId: string,
  query: PrometheusQueryDefinition,
  options: RunPrometheusQueryOptions = {},
): Promise<PrometheusQueryRun> {
  const submitted = await submitPrometheusQuery(
    clusterId,
    query,
    options.signal,
  );
  const command = await pollCommand(submitted.receipt.command_id, options);

  if (command.status === 'failed') {
    throw new MetricQueryExecutionError(
      'failed',
      commandFailureMessage(command),
      commandSummary(command),
    );
  }
  if (command.action !== TELEMETRY_QUERY_ACTION) {
    throw invalidTelemetryPayload(
      'Completed command had an unexpected action.',
    );
  }

  const parsed = telemetryCommandResultSchema.safeParse(command.result);
  if (!parsed.success) {
    throw invalidTelemetryPayload(
      'Completed metric command did not match the telemetry result contract.',
      parsed.error,
    );
  }
  if (parsed.data.query.name !== submitted.query.name) {
    throw invalidTelemetryPayload(
      'Completed metric command returned a different query name.',
    );
  }

  const result = parsed.data.result.results[submitted.query.name];
  if (result === undefined || result.point_count === 0) {
    throw new MetricQueryExecutionError(
      'empty-result',
      'Metric query completed without any observed points.',
      commandSummary(command),
    );
  }

  return {
    queryName: submitted.query.name,
    receipt: submitted.receipt,
    command: commandSummary(command),
    result,
  };
}

function assertUsageLimit(limit: number): void {
  if (
    !Number.isInteger(limit) ||
    limit < MIN_USAGE_LIMIT ||
    limit > MAX_USAGE_LIMIT
  ) {
    throw new RangeError(
      `usage limit must be an integer from 1 to ${MAX_USAGE_LIMIT}`,
    );
  }
}

function commandFailureMessage(command: CommandStatus): string {
  const message = command.result.message;
  return typeof message === 'string' && message.trim() !== ''
    ? message
    : 'Metric query command failed.';
}

function commandSummary(command: CommandStatus): MetricCommandSummary {
  const {result: _result, ...summary} = command;
  return summary;
}

function invalidTelemetryPayload(message: string, cause?: unknown): ApiError {
  return new ApiError('invalid-payload', message, {cause});
}

function waitForNextPoll(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(abortReason(signal));

  return new Promise((resolve, reject) => {
    const timeoutId = globalThis.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, delayMs);

    function onAbort(): void {
      globalThis.clearTimeout(timeoutId);
      signal?.removeEventListener('abort', onAbort);
      reject(
        signal === undefined
          ? new DOMException('Aborted', 'AbortError')
          : abortReason(signal),
      );
    }

    signal?.addEventListener('abort', onAbort, {once: true});
  });
}

function abortReason(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException('Aborted', 'AbortError');
}
