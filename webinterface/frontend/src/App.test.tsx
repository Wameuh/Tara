import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import "./i18n";
import { readSecret } from "./routing/secret";

const config = { language: "fr", locale: "fr-FR", supported_languages: ["fr"], input_modes: ["audio", "merged_transcription", "zip"], max_upload_bytes: 1, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 };
describe("App", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  it("fetches configuration once and renders translated creation controls", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
      String(input) === "/api/v1/funding/monthly" ? { enabled: false } : config,
    )));
    vi.stubGlobal("fetch", fetcher);
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Préparer une session Tara" })).toBeInTheDocument();
    expect(screen.getByLabelText("Version alpha")).toHaveTextContent("Service en phase d’essai");
    expect(fetcher.mock.calls.filter(([url]) => String(url) === "/api/v1/config/public")).toHaveLength(1);
    await vi.waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url) === "/api/v1/funding/monthly")).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Lancer l’analyse" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Sélectionnez au moins une piste");
  });
  it("opens the integrated help and returns to creation", async () => { vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(config)))); render(<App />); fireEvent.click(await screen.findByRole("button", { name: "Aide" })); expect(screen.getByRole("heading", { name: "Utiliser Tara en toute sécurité" })).toBeInTheDocument(); expect(screen.getByText("Session avant lancement · 24 h")).toBeInTheDocument(); expect(screen.getByText("Résultat final · 7 jours")).toBeInTheDocument(); expect(screen.getByText(/pas encore de purge automatique/)).toBeInTheDocument(); expect(location.pathname).toBe("/help"); fireEvent.click(screen.getByRole("button", { name: "Préparer une analyse" })); expect(screen.getByRole("heading", { name: "Préparer une session Tara" })).toBeInTheDocument(); });
  it("only renders input modes published by the server", async () => { vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...config, input_modes: ["audio"] })))); render(<App />); expect(await screen.findByRole("radio", { name: "Pistes audio" })).toBeInTheDocument(); expect(screen.queryByRole("radio", { name: "Archive ZIP audio" })).not.toBeInTheDocument(); });
  it("keeps a refreshable owner proof in the fragment", () => { const secret = "abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMN123456789"; history.replaceState(null, "", `/jobs/job_abcdefghijklmnop#secret=${secret}`); expect(readSecret()).toBe(secret); expect(location.hash).toContain("secret="); });
});
