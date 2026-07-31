import { ApiError } from "./client";
import {
  podTerminalEventSchema,
  type PodTerminalEndpointEvent,
} from "./pod-terminal-schemas";

const TERMINAL_PATH = "/api/live/terminal";
const MAX_COMMAND_LENGTH = 1_024;
const MAX_INPUT_LENGTH = 4_096;

export interface PodTerminalEndpointTarget {
  workspaceId: string;
  clusterId: string;
  namespace: string;
  pod: string;
  container: string;
}

export interface PodTerminalEndpointHandlers {
  onEvent: (event: PodTerminalEndpointEvent) => void;
  onFailure: (error: unknown) => void;
}

export interface PodTerminalEndpointConnection {
  sendInput(data: string): void;
  close(): void;
}

interface BrowserLocation {
  protocol: string;
  host: string;
}

export function buildPodTerminalUrl(
  target: PodTerminalEndpointTarget,
  browserLocation: BrowserLocation = window.location,
): string {
  const protocol = browserLocation.protocol === "https:"
    ? "wss:"
    : browserLocation.protocol === "http:"
      ? "ws:"
      : invalid("Pod terminal requires an HTTP or HTTPS page origin.");
  const url = new URL(TERMINAL_PATH, `${protocol}//${browserLocation.host}`);
  for (const [key, value] of [
    ["workspace_id", target.workspaceId],
    ["cluster_id", target.clusterId],
    ["namespace", target.namespace],
    ["pod", target.pod],
    ["container", target.container],
  ] as const) {
    const normalized = value.trim();
    if (!normalized) invalid(`Pod terminal ${key} is required.`);
    url.searchParams.set(key, normalized);
  }
  return url.toString();
}

export function openPodTerminal(
  target: PodTerminalEndpointTarget,
  command: string,
  handlers: PodTerminalEndpointHandlers,
): PodTerminalEndpointConnection {
  const normalizedCommand = command.trim();
  if (!normalizedCommand || normalizedCommand.length > MAX_COMMAND_LENGTH) {
    invalid("Pod terminal command is invalid.");
  }
  const socket = new WebSocket(buildPodTerminalUrl(target));
  let sessionId: string | null = null;
  let terminal = false;
  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ type: "terminal.start", command: normalizedCommand }));
  });
  socket.addEventListener("message", (event) => {
    try {
      const parsed = podTerminalEventSchema.parse(JSON.parse(String(event.data)));
      if (parsed.session_id) sessionId = parsed.session_id;
      handlers.onEvent(parsed);
      if (parsed.type === "terminal.end" || parsed.type === "terminal.error") {
        terminal = true;
        socket.close(1000, "Terminal session ended");
      }
    } catch (cause) {
      terminal = true;
      socket.close(1002, "Invalid terminal.v1 payload");
      handlers.onFailure(new ApiError("invalid-payload", "Invalid terminal response.", { cause }));
    }
  });
  socket.addEventListener("error", () => {
    if (!terminal) {
      terminal = true;
      handlers.onFailure(new ApiError("network", "Terminal transport failed."));
    }
  });
  socket.addEventListener("close", () => {
    if (!terminal) {
      terminal = true;
      handlers.onFailure(new ApiError("network", "Terminal connection closed unexpectedly."));
    }
  });
  return {
    sendInput(data) {
      if (!sessionId || !data || data.length > MAX_INPUT_LENGTH || socket.readyState !== WebSocket.OPEN) {
        invalid("Terminal input is not currently accepted.");
      }
      socket.send(JSON.stringify({ type: "terminal.input", session_id: sessionId, data }));
    },
    close() {
      terminal = true;
      if (sessionId && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "terminal.close", session_id: sessionId }));
      }
      if (socket.readyState < WebSocket.CLOSING) socket.close(1000, "Terminal closed by operator");
    },
  };
}

function invalid(message: string): never {
  throw new ApiError("invalid-request", message);
}
