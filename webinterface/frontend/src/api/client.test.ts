import { describe, expect, it, vi, afterEach } from "vitest";
import { api, ApiError, fetchPublicConfig, isPublicConfig } from "./client";

const valid = { language: "fr", locale: "fr-FR", supported_languages: ["fr"], input_modes: ["audio", "merged_transcription", "zip"], max_upload_bytes: 1, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 };
afterEach(() => vi.unstubAllGlobals());
describe("public config client", () => {
  it("creates a session with the selected exclusive input type", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ session_id: "session", secret: "secret", revision: 1 })));
    vi.stubGlobal("fetch", fetch);
    await api.createUploadSession("merged_transcription");
    const [path, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe("/api/v1/uploads/sessions?input_type=merged_transcription");
    expect(init).toMatchObject({ method: "POST" });
    expect(init.body).toBeUndefined();
    expect(init.headers).not.toMatchObject({ "Content-Type": "application/json" });
  });
  it("sends resumable chunks as declared binary bodies", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ confirmed_offset: 3, revision: 2 })));
    vi.stubGlobal("fetch", fetch);
    const body = new Blob(["abc"]);
    await api.uploadChunk("session", "file", "secret", 0, "a".repeat(64), body);
    const [path, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toContain("/chunks");
    expect(init.method).toBe("PATCH");
    expect(init.body).toBe(body);
    expect(init.headers).toMatchObject({ "Content-Type": "application/octet-stream", "Upload-Offset": "0", "Upload-Checksum": "a".repeat(64) });
  });
  it("accepts a valid response", async () => { vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(valid)))); await expect(fetchPublicConfig()).resolves.toEqual(valid); });
  it("preserves the stable API error and correlation identifier", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ code: "upload_chunk_conflict", correlation_id: "support_123" }), { status: 409, headers: { "Content-Type": "application/problem+json" } })));
    const error = await api.fileOffset("session", "secret", "file").catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "upload_chunk_conflict", correlationId: "support_123" });
  });
  it("rejects non-ok and invalid JSON", async () => { vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 500 }))); await expect(fetchPublicConfig()).rejects.toThrow(); vi.stubGlobal("fetch", vi.fn(async () => new Response("{"))); await expect(fetchPublicConfig()).rejects.toThrow(); });
  it.each([{},{ ...valid, max_upload_bytes: 0 },{ ...valid, max_upload_bytes: 1.5 },{ ...valid, supported_languages: [] },{ ...valid, supported_languages: ["fr", "fr"] },{ ...valid, supported_languages: [1] },{ ...valid, supported_languages: ["../en"] },{ ...valid, supported_languages: ["EN"] },{ ...valid, supported_languages: [""] },{ ...valid, supported_languages: ["en"] },{ ...valid, input_modes: [] },{ ...valid, input_modes: ["audio", "audio"] },{ ...valid, input_modes: ["unknown"] },{ ...valid, language: "FR" },{ ...valid, locale: "en-US" }])("rejects malformed payload", (value) => expect(isPublicConfig(value)).toBe(false));
});
