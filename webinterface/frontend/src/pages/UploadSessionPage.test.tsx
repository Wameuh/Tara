import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PublicConfig } from "../api/client";
import "../i18n";
import { clearPending, setPending } from "../features/upload/pending";
import { UploadSessionPage } from "./UploadSessionPage";

const config: PublicConfig = { language: "fr", locale: "fr-FR", supported_languages: ["fr"], input_modes: ["audio", "merged_transcription", "zip"], max_upload_bytes: 1, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 };

describe("UploadSessionPage audio", () => {
  afterEach(() => { cleanup(); clearPending("audio-session"); vi.unstubAllGlobals(); });

  it("keeps the person editable after upload and persists the change", async () => {
    setPending("audio-session", [{ key: "audio", file: new File(["audio"], "Alice.mp3"), inputKind: "audio", person: "Alice", state: "done", hashingLoaded: 5, confirmedOffset: 5, idempotencyKey: "key", fileId: "audio-file", fileRevision: 3 }]);
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/person")) return new Response(JSON.stringify({ revision: 4 }));
      return new Response(JSON.stringify({
        session_id: "audio-session", revision: 4, status: "ready", expires_at: "2030-01-01T00:00:00Z", language: "fr", context_text: "", previous_summaries_text: "", validations: [], allowed_actions: ["launch"], input_type: "audio",
        files: [{ file_id: "audio-file", revision: 3, status: "ready", confirmed_offset: 5, total_size: 5, display_name: "Alice.mp3", person: "Alice", allowed_actions: ["change_person"] }],
      }), { headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetcher);
    render(<UploadSessionPage sessionId="audio-session" secret="secret" config={config} go={() => undefined} autoLaunch={false} />);
    expect(await screen.findByText(/Ce lien équivaut à un accès propriétaire/)).toBeInTheDocument();
    const person = await screen.findByLabelText("Personne");
    fireEvent.change(person, { target: { value: "Alicia" } });
    fireEvent.blur(person);
    await vi.waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/person"))).toBe(true));
  });

  it("resynchronizes the confirmed offset after a chunk conflict", async () => {
    const file = new File(["abc"], "Alice.mp3");
    const chunk = new Blob(["abc"]);
    Object.defineProperty(chunk, "arrayBuffer", { value: async () => new Uint8Array([97, 98, 99]).buffer });
    vi.spyOn(file, "slice").mockReturnValue(chunk);
    setPending("audio-session", [{ key: "audio", file, inputKind: "audio", person: "Alice", state: "queued", hashingLoaded: 3, confirmedOffset: 0, idempotencyKey: "key", fileId: "audio-file", fileRevision: 1 }]);
    vi.stubGlobal("crypto", { randomUUID: () => "test-id", subtle: { digest: async () => new Uint8Array(32).buffer } });
    let offsetRequests = 0;
    let chunkRequests = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/offset")) {
        offsetRequests += 1;
        return new Response(JSON.stringify({ confirmed_offset: offsetRequests === 1 ? 0 : 1, revision: offsetRequests === 1 ? 1 : 2 }));
      }
      if (url.endsWith("/chunks")) {
        chunkRequests += 1;
        if (chunkRequests === 1) return new Response(JSON.stringify({ code: "upload_chunk_conflict", correlation_id: "conflict-1" }), { status: 409 });
        return new Response(JSON.stringify({ confirmed_offset: 3, revision: 3 }));
      }
      if (url.endsWith("/person")) return new Response(JSON.stringify({ revision: 4 }));
      if (url.endsWith("/finalize")) return new Response(JSON.stringify({ accepted: true }), { status: 202 });
      return new Response(JSON.stringify({
        session_id: "audio-session", revision: 1, status: "uploading", expires_at: "2030-01-01T00:00:00Z", language: "fr", context_text: "", previous_summaries_text: "", validations: [], allowed_actions: ["cancel"], input_type: "audio",
        files: [{ file_id: "audio-file", revision: 1, status: "uploading", confirmed_offset: 0, total_size: 3, display_name: "Alice.mp3", person: "Alice", allowed_actions: [] }],
      }));
    });
    vi.stubGlobal("fetch", fetcher);
    render(<UploadSessionPage sessionId="audio-session" secret="secret" config={{ ...config, max_upload_bytes: 10 }} go={() => undefined} autoLaunch={false} />);
    await vi.waitFor(() => expect(chunkRequests).toBe(2));
    expect(offsetRequests).toBeGreaterThanOrEqual(2);
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/finalize"))).toBe(true);
  });

  it("shows expected, supplied and detected formats after server validation", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      session_id: "audio-session", revision: 2, status: "invalid", expires_at: "2030-01-01T00:00:00Z", language: "fr", context_text: "", previous_summaries_text: "", validations: [], allowed_actions: [], input_type: "audio",
      files: [{ file_id: "audio-file", revision: 2, status: "invalid", confirmed_offset: 5, total_size: 5, display_name: "Alice.mp3", person: "Alice", allowed_actions: [], error: { code: "input_invalid", message_key: "upload.audio_format_mismatch", parameters: { expected: "MP3, OGG, AAC, M4A", provided: "MP3", detected: "OGG" } } }],
    }))));

    render(<UploadSessionPage sessionId="audio-session" secret="secret" config={config} go={() => undefined} autoLaunch={false} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Formats attendus : MP3, OGG, AAC, M4A");
    expect(screen.getByRole("alert")).toHaveTextContent("Format fourni : MP3 ; format détecté : OGG");
  });
});

describe("UploadSessionPage merged transcription", () => {
  afterEach(() => { cleanup(); clearPending("session"); vi.unstubAllGlobals(); });

  it("shows metadata from the YAML snapshot file that matches local pending state", async () => {
    setPending("session", [{ key: "pending", file: new File(["yaml"], "merged.yaml"), inputKind: "merged_transcription", state: "done", hashingLoaded: 4, confirmedOffset: 4, idempotencyKey: "key", fileId: "yaml-file", fileRevision: 1 }]);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      session_id: "session", revision: 1, status: "validating", expires_at: "2030-01-01T00:00:00Z", language: "fr", context_text: "", previous_summaries_text: "", validations: [], allowed_actions: [], input_type: "merged_transcription",
      files: [{ file_id: "yaml-file", revision: 1, status: "invalid", confirmed_offset: 12, total_size: 12, display_name: "merged.yaml", person: null, allowed_actions: [], schema_name: "tara.merged_transcription", schema_version: "26.0.1", token_count: 4242, error: { code: "input_invalid", message_key: "upload.validation_error", parameters: { path: "content.segments[2]" } } }],
    }))));
    render(<UploadSessionPage sessionId="session" secret="secret" config={config} go={() => undefined} />);
    expect(await screen.findByText("Schema : tara.merged_transcription")).toBeInTheDocument();
    expect(screen.getByText("Version du schema : 26.0.1")).toBeInTheDocument();
    expect(screen.getByText("Tokens mesures : 4242")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("content.segments[2]");
  });
});

describe("UploadSessionPage ZIP", () => {
  afterEach(() => { cleanup(); clearPending("zip-session"); vi.unstubAllGlobals(); });

  it("waits for explicit association confirmation before launching", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/person")) return new Response(JSON.stringify({ revision: 2 }));
      if (url.endsWith("/jobs")) return new Response(JSON.stringify({ job_id: "job_zip" }));
      return new Response(JSON.stringify({
        session_id: "zip-session", revision: 4, status: "ready", expires_at: "2030-01-01T00:00:00Z", language: "fr", context_text: "", previous_summaries_text: "", validations: [], allowed_actions: ["cancel", "launch"], input_type: "zip", archive_excluded_count: 2, archive_phase: "launch_preparation",
        files: [{ file_id: "track", revision: 1, status: "ready", confirmed_offset: 5, total_size: 5, display_name: "Alice.mp3", archive_entry_name: "table/Alice.mp3", person: "Alice", allowed_actions: ["delete_file", "change_person"] }],
      }), { headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetcher);
    const go = vi.fn();
    render(<UploadSessionPage sessionId="zip-session" secret="secret" config={config} go={go} />);
    expect(await screen.findByText("table/Alice.mp3")).toBeInTheDocument();
    expect(screen.getByText("2 fichier(s) non audio ignoré(s).")).toBeInTheDocument();
    expect(screen.getByText("Préparation du lancement")).toHaveAttribute("aria-current", "step");
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/jobs"))).toBe(false);
    fireEvent.change(screen.getByLabelText("Personne"), { target: { value: "Alicia" } });
    fireEvent.click(screen.getByRole("button", { name: "Valider les associations et lancer" }));
    await vi.waitFor(() => expect(go).toHaveBeenCalled());
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/person"))).toBe(true);
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/jobs"))).toBe(true);
  });

  it("explains when an archive contains no supported audio", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      session_id: "zip-session", revision: 2, status: "invalid", expires_at: "2030-01-01T00:00:00Z", language: "fr", context_text: "", previous_summaries_text: "", validations: [], allowed_actions: [], input_type: "zip", archive_phase: "extraction",
      files: [{ file_id: "archive", revision: 2, status: "invalid", confirmed_offset: 10, total_size: 10, display_name: "recordings.zip", person: null, allowed_actions: [], error: { code: "input_invalid", message_key: "upload.zip_no_supported_audio", parameters: {} } }],
    }))));

    render(<UploadSessionPage sessionId="zip-session" secret="secret" config={config} go={() => undefined} autoLaunch={false} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("L’archive ne contient aucune piste MP3, OGG, AAC ou M4A.");
  });
});
