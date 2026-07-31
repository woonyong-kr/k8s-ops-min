import { ApiError, apiRequest, type ApiPath } from "./client";
import {
  pollCommand,
  type MetricCommandSummary,
} from "./metrics";
import {
  agentDebugQueryReceiptSchema,
  type CommandStatus,
} from "./metrics-schemas";
import {
  telemetryCommandResultSchema,
  telemetryQueryDefinitionSchema,
  type TelemetryCommandResult,
  type TelemetryQueryDefinition,
} from "./telemetry-schemas";

const TELEMETRY_QUERY_ACTION = "telemetry.query.run";

export interface RunTelemetryQueryOptions {
  signal?: AbortSignal;
}

export interface TelemetryQueryRun {
  queryName: string;
  receipt: {
    accepted: true;
    command_id: string;
    correlation_id: string;
  };
  command: MetricCommandSummary;
  result: TelemetryCommandResult["result"];
}

/**
 * Runs one backend-owned Loki query and returns a real Pod log snapshot.
 * The POST is issued once; completion is observed through command polling.
 */
export async function runTelemetryQuery(
  clusterId: string,
  query: TelemetryQueryDefinition,
  options: RunTelemetryQueryOptions = {},
): Promise<TelemetryQueryRun> {
  const validatedQuery = telemetryQueryDefinitionSchema.parse(query);
  const receipt = await apiRequest(
    "/api/agent/debug/query" as ApiPath,
    agentDebugQueryReceiptSchema,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ cluster_id: clusterId, query: validatedQuery }),
      signal: options.signal,
    },
  );
  const command = await pollCommand(receipt.command_id, options);

  if (command.status === "failed") {
    throw new TelemetryQueryExecutionError(
      "failed",
      telemetryFailureMessage(command),
      commandSummary(command),
    );
  }
  if (command.action !== TELEMETRY_QUERY_ACTION) {
    throw invalidTelemetryPayload("Completed command had an unexpected action.");
  }

  const parsed = telemetryCommandResultSchema.safeParse(command.result);
  if (!parsed.success) {
    throw invalidTelemetryPayload(
      "Completed telemetry command did not match the log result contract.",
      parsed.error,
    );
  }
  if (parsed.data.query.name !== validatedQuery.name) {
    throw invalidTelemetryPayload(
      "Completed telemetry command returned a different query name.",
    );
  }

  return {
    queryName: validatedQuery.name,
    receipt,
    command: commandSummary(command),
    result: parsed.data.result,
  };
}

export class TelemetryQueryExecutionError extends Error {
  readonly kind: "failed";
  readonly command: MetricCommandSummary;

  constructor(kind: "failed", message: string, command: MetricCommandSummary) {
    super(message);
    this.name = "TelemetryQueryExecutionError";
    this.kind = kind;
    this.command = command;
  }
}

function telemetryFailureMessage(command: CommandStatus): string {
  const message = command.result.message;
  return typeof message === "string" && message.trim() !== ""
    ? message
    : "Telemetry query command failed.";
}

function commandSummary(command: CommandStatus): MetricCommandSummary {
  const { result: _result, ...summary } = command;
  return summary;
}

function invalidTelemetryPayload(message: string, cause?: unknown): ApiError {
  return new ApiError("invalid-payload", message, { cause });
}
