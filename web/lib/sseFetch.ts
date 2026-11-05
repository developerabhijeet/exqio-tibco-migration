// POST a JSON body and read the text/event-stream answer: EventSource cannot send a body or the
// X-User header. Frames are `event:` / `data:` lines ended by a blank line; each event goes to its
// handler with the parsed JSON. Non-2xx answers throw ApiError (the 429 text surfaces).
import { request } from "./api";

export type SseHandlers = Record<string, (data: any) => void>; // eslint-disable-line @typescript-eslint/no-explicit-any

/** One frame (the lines between blank lines) as [event, data], or null for a comment or keep-alive. */
export function parseFrame(frame: string): [string, unknown] | null {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (data.length === 0) return null;
  try {
    return [event, JSON.parse(data.join("\n"))];
  } catch {
    return null;
  }
}

export async function sseFetch(path: string, body: unknown, handlers: SseHandlers, signal?: AbortSignal): Promise<void> {
  const res = await request(path, { method: "POST", body: JSON.stringify(body), signal, headers: { accept: "text/event-stream" } });
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const dispatch = (frame: string) => {
    const parsed = parseFrame(frame);
    if (parsed) handlers[parsed[0]]?.(parsed[1]);
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    frames.forEach(dispatch);
  }
  buffer += decoder.decode();
  if (buffer.trim()) dispatch(buffer);
}
