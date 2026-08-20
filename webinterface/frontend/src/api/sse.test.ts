import { afterEach, describe, expect, it, vi } from "vitest";
import { isEventEnvelope, streamEvents } from "./sse";

afterEach(() => vi.unstubAllGlobals());

describe("SSE envelope", () => {
  it("accepts a bounded public revision and rejects malformed data", () => {
    expect(isEventEnvelope({ type: "snapshot_updated", revision: 2, data: {} })).toBe(true);
    expect(isEventEnvelope({ type: "oops", revision: -1, data: [] })).toBe(false);
  });

  it("parses CRLF and multiline data split across network chunks", async () => {
    const encoder = new TextEncoder();
    const chunks = [
      "data: {\"type\":\"snapshot_updated\",\r",
      "\n",
      "data: \"revision\":3,\"data\":{}}\r",
      "\n\r\n",
      "data: malformed\n\n",
      "data: {\"type\":\"run_completed\",\"revision\":4,\"data\":{}}\n\n",
    ];
    const body = new ReadableStream({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { headers: { "content-type": "text/event-stream; charset=utf-8" } })));
    const events: number[] = [];
    await streamEvents("/events", "secret", (event) => events.push(event.revision), new AbortController().signal);
    expect(events).toEqual([3, 4]);
  });
});
