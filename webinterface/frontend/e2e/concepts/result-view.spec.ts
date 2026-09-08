import { expect, test } from "@playwright/test";

const compareVisualSnapshots = !process.env.CI;
const secret = "s".repeat(43);
const config = { language: "fr", locale: "fr-FR", supported_languages: ["fr"], input_modes: ["audio", "merged_transcription", "zip"], max_upload_bytes: 1024, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 };
const completed = { job_id: "job_abcdefghijklmnop", status: "completed", revision: 4, attempt_number: 1, language: "fr", allowed_actions: [], identical_relaunch_available: false, inputs: [], warnings: [], expires_at: "2026-07-24T12:00:00Z", progress: null, stages: [] };
const result = { type: "tara_result_v1", status: "available", expires_at: "2026-07-24T12:00:00Z", cost: { status: "available", value_micro_eur: 0 }, sections: [
  { id: "overview", section_type: "overview", status: "available", title: "Vue d'ensemble", text: "Le groupe entre dans la cite.", blocks: [{ type: "paragraph", text: "Le groupe entre dans la cite." }], order: 0 },
  { id: "scenes", section_type: "chronology", status: "available", title: "Scenes", text: "Une scene avec un dragon et un indice.", blocks: [{ type: "paragraph", text: "Une scene avec un dragon et un indice." }], order: 2 },
  { id: "characters", section_type: "characters", status: "available", title: "Personnages", text: "Aline recherche l'indice perdu.", blocks: [{ type: "paragraph", text: "Aline recherche l'indice perdu." }], order: 1 },
] };

test("resultat: recherche, accordions, navigation et fragment secret", async ({ page }, testInfo) => {
  await page.route("**/api/v1/config/public", route => route.fulfill({ json: config }));
  await page.route("**/events", route => route.fulfill({ status: 503 }));
  await page.route(/\/api\/v1\/jobs\/job_abcdefghijklmnop(?:\?.*)?$/, route => route.fulfill({ json: completed }));
  await page.route("**/api/v1/jobs/job_abcdefghijklmnop/result", route => route.fulfill({ json: result }));
  await page.goto(`/jobs/job_abcdefghijklmnop#secret=${secret}`);
  await expect(page.getByRole("heading", { name: "Analyse Tara terminée" })).toBeVisible();
  await page.getByRole("searchbox").fill("indice");
  await expect(page.getByText(/2 correspondance/)).toBeVisible();
  await page.getByRole("button", { name: "Tout fermer" }).click();
  await page.getByRole("button", { name: "Tout ouvrir" }).click();
  await expect(page.locator("button button")).toHaveCount(0);
  for (const viewport of [{ width: 1440, height: 900 }, { width: 980, height: 900 }, { width: 390, height: 844 }, { width: 320, height: 720 }]) {
    await page.setViewportSize(viewport);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(page.locator("button button, a button, button a")).toHaveCount(0);
    if (testInfo.project.name === "chromium" && compareVisualSnapshots) {
      await expect(page).toHaveScreenshot(`result-view-${viewport.width}.png`, {
        fullPage: true,
        animations: "disabled",
        maxDiffPixelRatio: 0.01,
      });
    }
  }
});
