import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import "../i18n";
import { clearPending, setPending } from "../features/upload/pending";
import { UploadSessionPage } from "./UploadSessionPage";

const config = { language: "fr", locale: "fr-FR", supported_languages: ["fr"], max_upload_bytes: 1, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 };

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
});
