import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import "./i18n";
import { readSecret } from "./routing/secret";

const config = { language: "fr", locale: "fr-FR", supported_languages: ["fr"], max_upload_bytes: 1, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 };
describe("App", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  it("fetches configuration once and renders translated creation controls", async () => { const fetcher = vi.fn(async () => new Response(JSON.stringify(config))); vi.stubGlobal("fetch", fetcher); render(<App />); expect(await screen.findByRole("heading", { name: "Préparer une session Tara" })).toBeInTheDocument(); expect(fetcher).toHaveBeenCalledTimes(1); fireEvent.click(screen.getByRole("button", { name: "Lancer l’analyse" })); expect(screen.getByRole("alert")).toHaveTextContent("Sélectionnez au moins une piste"); });
  it("keeps a refreshable owner proof in the fragment", () => { const secret = "abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMN123456789"; history.replaceState(null, "", `/jobs/job_abcdefghijklmnop#secret=${secret}`); expect(readSecret()).toBe(secret); expect(location.hash).toContain("secret="); });
});
