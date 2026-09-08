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

  it("shows expected and supplied formats before uploading an invalid file", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const { container } = render(<NewJobPage config={config} go={() => undefined} />);
    const input = container.querySelector<HTMLInputElement>('input[type="file"][multiple][accept^="audio/"]');
    fireEvent.change(input!, { target: { files: [new File(["audio"], "Alice.wav", { type: "audio/wav" })] } });

    expect(await screen.findByRole("alert")).toHaveTextContent("Formats attendus : MP3, OGG, AAC, M4A");
    expect(screen.getByRole("alert")).toHaveTextContent("Formats fournis : WAV · audio/wav");
    expect(fetcher.mock.calls.some(([url]) => /\/uploads\/sessions/.test(String(url)))).toBe(false);
  });

  it("detects ZIP and YAML inputs without asking for a source type", () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<NewJobPage config={config} go={() => undefined} />);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();

    const input = screen.getByLabelText("Déposez vos fichiers ici");
    fireEvent.change(input, { target: { files: [new File(["zip"], "craig-session.zip", { type: "application/zip" })] } });
    expect(screen.getByText("Source détectée : Archive ZIP audio")).toBeVisible();
    expect(screen.getByText("craig-session.zip")).toBeVisible();

    fireEvent.change(input, { target: { files: [new File(["schema: tara"], "session.yaml", { type: "application/yaml" })] } });
    expect(screen.getByText("Source détectée : Transcription fusionnée")).toBeVisible();
    expect(screen.getByText("session.yaml")).toBeVisible();
    expect(screen.queryByText("craig-session.zip")).not.toBeInTheDocument();
  });

  it("detects dropped audio and rejects mixed source types before uploading", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      if (/\/uploads\/sessions\?input_type=audio$/.test(String(input))) return new Response(JSON.stringify({ session_id: "session_audio", secret: "secret", revision: 1 }));
      return new Promise<Response>(() => undefined);
    });
    vi.stubGlobal("fetch", fetcher);
    render(<NewJobPage config={config} go={() => undefined} />);
    const dropzone = screen.getByText("Déposez vos fichiers ici").closest("label");
    expect(dropzone).not.toBeNull();

    fireEvent.drop(dropzone!, { dataTransfer: { files: [new File(["audio"], "Alice.m4a", { type: "audio/mp4" })] } });
    await vi.waitFor(() => expect(getPending("session_audio")).toHaveLength(1));
    expect(screen.getByText("Source détectée : Pistes audio")).toBeVisible();

    fireEvent.drop(dropzone!, { dataTransfer: { files: [new File(["zip"], "other.zip", { type: "application/zip" })] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Le transfert audio a déjà commencé");
    expect(fetcher.mock.calls.filter(([url]) => /\/uploads\/sessions/.test(String(url)))).toHaveLength(1);
  });

  it("shows monthly Ko-fi donations beside Tara's cumulative estimates", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/v1/funding/monthly") return new Response(JSON.stringify({
        enabled: true,
        month: "2026-08",
        timezone: "Europe/Paris",
        currency: "EUR",
        donations_micro_eur: 32_500_000,
        estimated_consumption_micro_eur: 8_250_000,
        estimate_partial: false,
        monthly_goal_micro_eur: 30_000_000,
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
    const fundingBars = screen.getAllByRole("progressbar");
    expect(fundingBars).toHaveLength(2);
    for (const bar of fundingBars) expect(bar).toHaveAttribute("max", "30");
    expect(container.querySelector("form")?.nextElementSibling).toBe(fundingHeading.closest("section"));
    expect(screen.queryByText(/Reste à couvrir|Marge estimée/)).not.toBeInTheDocument();
  });
});
