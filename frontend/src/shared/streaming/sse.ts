export const MAX_SSE_FRAME_LENGTH = 64 * 1024;

export interface SseFrame {
  id: string | null;
  event: string | null;
  data: string;
}

export interface ParsedSseFrames {
  frames: SseFrame[];
  remainder: string;
}

export class SseFrameError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SseFrameError";
  }
}

/** Parse complete SSE frames while preserving a partial trailing frame for the next chunk. */
export function parseSseFrames(source: string): ParsedSseFrames {
  const frames: SseFrame[] = [];
  let remainder = source;
  while (true) {
    const boundary = /\r\n\r\n|\n\n|\r\r/u.exec(remainder);
    if (!boundary) break;
    const rawFrame = remainder.slice(0, boundary.index);
    if (rawFrame.length > MAX_SSE_FRAME_LENGTH) throw new SseFrameError("SSE frame exceeded limit");
    remainder = remainder.slice(boundary.index + boundary[0].length);
    const frame = parseFrame(rawFrame);
    if (frame !== null) frames.push(frame);
  }
  if (remainder.length > MAX_SSE_FRAME_LENGTH) throw new SseFrameError("SSE frame exceeded limit");
  return { frames, remainder };
}

function parseFrame(rawFrame: string): SseFrame | null {
  const normalized = rawFrame.replace(/\r\n|\r/gu, "\n");
  let id: string | null = null;
  let event: string | null = null;
  const data: string[] = [];
  for (const line of normalized.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    const value = separator < 0 ? "" : line.slice(separator + 1).replace(/^ /u, "");
    if (field === "data") {
      data.push(value);
    } else if (field === "id") {
      id = value.includes("\0") ? null : value;
    } else if (field === "event") {
      event = value;
    } else if (field !== "retry") {
      throw new SseFrameError("unsupported SSE field");
    }
  }
  return data.length > 0 ? { id, event, data: data.join("\n") } : null;
}
