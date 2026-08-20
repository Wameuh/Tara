export type EventEnvelope = {
  type: "snapshot_updated" | "warning_raised" | "run_completed" | "run_failed";
  revision: number;
  data: Record<string, string | number | boolean | null>;
};

export function isEventEnvelope(value: unknown): value is EventEnvelope {
  if (!value || typeof value !== "object") return false;
  const event = value as Record<string, unknown>;
  return ["snapshot_updated", "warning_raised", "run_completed", "run_failed"].includes(String(event.type))
    && Number.isInteger(event.revision)
    && (event.revision as number) >= 0
    && !!event.data
    && typeof event.data === "object"
    && !Array.isArray(event.data);
}

function dispatchFrame(frame: string, onEvent: (event: EventEnvelope) => void): void {
  const data = frame
    .split("\n")
    .filter((line) => line === "data" || line.startsWith("data:"))
    .map((line) => line.slice(5).replace(/^ /, ""))
    .join("\n");
  if (!data) return;
  try {
    const parsed: unknown = JSON.parse(data);
    if (isEventEnvelope(parsed)) onEvent(parsed);
  } catch {
    // An invalid public event must not interrupt later valid updates.
  }
}

export async function streamEvents(
  url: string,
  secret: string,
  onEvent: (event: EventEnvelope) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(url, {
    headers: { Accept: "text/event-stream", "X-Tara-Job-Secret": secret },
    cache: "no-store",
    signal,
  });
  if (!response.ok || !response.headers.get("content-type")?.startsWith("text/event-stream") || !response.body) {
    throw new Error("sse_unavailable");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (!signal.aborted) {
    const chunk = await reader.read();
    buffer += decoder.decode(chunk.value, { stream: !chunk.done });
    const splitCarriageReturn = !chunk.done && buffer.endsWith("\r");
    const normalizable = splitCarriageReturn ? buffer.slice(0, -1) : buffer;
    buffer = normalizable.replace(/\r\n|\r/g, "\n") + (splitCarriageReturn ? "\r" : "");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      dispatchFrame(buffer.slice(0, boundary), onEvent);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (chunk.done) break;
  }
  if (buffer.trim()) dispatchFrame(buffer, onEvent);
}
