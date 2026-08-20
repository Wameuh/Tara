import { afterEach, describe, expect, it, vi } from "vitest";
import { hashFile } from "./hashFile";

class WorkerMock {
  static latest: WorkerMock;
  onerror: (() => void) | null = null;
  onmessageerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  terminated = false;
  request: { id: string } | null = null;
  constructor() { WorkerMock.latest = this; }
  postMessage(request: { id: string }) { this.request = request; }
  terminate() { this.terminated = true; }
}

afterEach(() => vi.unstubAllGlobals());

describe("incremental file hashing", () => {
  it("reports progress and terminates its worker on completion", async () => {
    vi.stubGlobal("Worker", WorkerMock);
    const progress = vi.fn();
    const promise = hashFile(new File(["abc"], "track.mp3"), progress, new AbortController().signal);
    const id = WorkerMock.latest.request!.id;
    WorkerMock.latest.onmessage?.({ data: { type: "progress", id, loaded: 3 } } as MessageEvent);
    WorkerMock.latest.onmessage?.({ data: { type: "complete", id, digest: "abc" } } as MessageEvent);
    await expect(promise).resolves.toBe("abc");
    expect(progress).toHaveBeenCalledWith(3);
    expect(WorkerMock.latest.terminated).toBe(true);
  });

  it("rejects immediately when already cancelled", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(hashFile(new File([], "track.mp3"), vi.fn(), controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  });

  it.each(["error", "messageerror"])("uses one public failure code for worker %s", async (kind) => {
    vi.stubGlobal("Worker", WorkerMock);
    const promise = hashFile(new File(["abc"], "track.mp3"), vi.fn(), new AbortController().signal);
    if (kind === "error") WorkerMock.latest.onerror?.(); else WorkerMock.latest.onmessageerror?.();
    await expect(promise).rejects.toThrow("hashing_unavailable");
  });
});
