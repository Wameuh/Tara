import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PublicConfig } from "../api/client";
import { clearPending, getPending } from "../features/upload/pending";
import "../i18n";
import { NewJobPage } from "./NewJobPage";

const config: PublicConfig = { language: "fr", locale: "fr-FR", supported_languages: ["fr"], input_modes: ["audio", "merged_transcription", "zip"], max_upload_bytes: 1024, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 };

describe("NewJobPage audio selection", () => {
  afterEach(() => { cleanup(); clearPending("session_audio"); vi.unstubAllGlobals(); });

  it("creates a session and queues upload immediately after files are selected", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/uploads/sessions")) return new Response(JSON.stringify({ session_id: "session_audio", secret: "secret", revision: 1 }));
      return new Response(JSON.stringify({ revision: 2 }));
    });
    vi.stubGlobal("fetch", fetcher);
    const go = vi.fn();
    const { container } = render(<NewJobPage config={config} go={go} />);
    const input = container.querySelector<HTMLInputElement>('input[type="file"][multiple][accept^="audio/"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, { target: { files: [new File(["audio"], "Alice.mp3", { type: "audio/mpeg" })] } });

    await vi.waitFor(() => expect(go).toHaveBeenCalledWith("/sessions/session_audio#secret=secret"));
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(getPending("session_audio")).toHaveLength(1);
    expect(getPending("session_audio")[0]).toMatchObject({ inputKind: "audio", person: "Alice", state: "queued" });
  });
});
