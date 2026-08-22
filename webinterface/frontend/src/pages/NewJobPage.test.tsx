import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PublicConfig } from "../api/client";
import { clearPending, getPending } from "../features/upload/pending";
import "../i18n";
import { NewJobPage } from "./NewJobPage";

const config: PublicConfig = { language: "fr", locale: "fr-FR", supported_languages: ["fr"], input_modes: ["audio", "merged_transcription", "zip"], max_upload_bytes: 1024, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 };

describe("NewJobPage audio selection", () => {
  afterEach(() => { cleanup(); clearPending("session_audio"); vi.unstubAllGlobals(); });

  it("uploads in the form while the user continues editing", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (/\/uploads\/sessions\?input_type=audio$/.test(url)) return new Response(JSON.stringify({ session_id: "session_audio", secret: "secret", revision: 1 }));
      return new Promise<Response>(() => undefined);
    });
    vi.stubGlobal("fetch", fetcher);
    const go = vi.fn();
    const { container } = render(<NewJobPage config={config} go={go} />);
    const input = container.querySelector<HTMLInputElement>('input[type="file"][multiple][accept^="audio/"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, { target: { files: [new File(["audio"], "Alice.mp3", { type: "audio/mpeg" })] } });

    await vi.waitFor(() => expect(getPending("session_audio")).toHaveLength(1));
    expect(go).not.toHaveBeenCalled();
    const contextInput = container.querySelector<HTMLTextAreaElement>(".context-field textarea");
    expect(contextInput).not.toBeNull();
    fireEvent.change(contextInput!, { target: { value: "Contexte ajouté pendant le transfert" } });
    expect(contextInput).toHaveValue("Contexte ajouté pendant le transfert");
    expect(getPending("session_audio")).toHaveLength(1);
    expect(getPending("session_audio")[0]).toMatchObject({ inputKind: "audio", person: "Alice" });

    fireEvent.change(input!, { target: { files: [new File(["more"], "Bob.ogg", { type: "audio/ogg" })] } });
    await vi.waitFor(() => expect(getPending("session_audio")).toHaveLength(2));
    expect(fetcher.mock.calls.filter(([url]) => /\/uploads\/sessions\?input_type=audio$/.test(String(url)))).toHaveLength(1);
  });

  it("shows the API correlation identifier as a support code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      code: "internal_error",
      correlation_id: "admin-log-42",
    }), { status: 500, headers: { "Content-Type": "application/problem+json" } })));
    const { container } = render(<NewJobPage config={config} go={() => undefined} />);
    const input = container.querySelector<HTMLInputElement>('input[type="file"][multiple][accept^="audio/"]');
    fireEvent.change(input!, { target: { files: [new File(["audio"], "Alice.mp3", { type: "audio/mpeg" })] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Une erreur est survenue. Code support : admin-log-42.");
  });

  it("shows monthly Ko-fi donations beside Tara's cumulative estimates", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/v1/funding/monthly") return new Response(JSON.stringify({
        enabled: true,
        month: "2026-08",
        timezone: "Europe/Paris",
        currency: "EUR",
        donations_micro_eur: 12_500_000,
        estimated_consumption_micro_eur: 8_250_000,
        estimate_partial: false,
        monthly_goal_micro_eur: 50_000_000,
        kofi_page_url: "https://ko-fi.com/tara",
      }));
      return new Response("{}", { status: 404 });
    }));

    const { container } = render(<NewJobPage config={config} go={() => undefined} />);

    const fundingHeading = await screen.findByRole("heading", { name: "Soutien Ko-fi et estimations Tara" });
    expect(fundingHeading).toBeVisible();
    expect(screen.getByText("Dons reçus")).toBeVisible();
    expect(screen.getByText("Consommation estimée")).toBeVisible();
    expect(screen.getByRole("link", { name: "Soutenir Tara sur Ko-fi" })).toHaveAttribute("href", "https://ko-fi.com/tara");
    expect(screen.getAllByRole("progressbar")).toHaveLength(2);
    expect(container.querySelector("form")?.nextElementSibling).toBe(fundingHeading.closest("section"));
    expect(screen.queryByText(/Reste à couvrir|Marge estimée/)).not.toBeInTheDocument();
  });
});
