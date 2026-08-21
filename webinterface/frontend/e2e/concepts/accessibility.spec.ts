import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const secret = "s".repeat(43);
const config = { language: "fr", locale: "fr-FR", supported_languages: ["fr"], input_modes: ["audio", "merged_transcription", "zip"], max_upload_bytes: 1024, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 };
const job = { job_id: "job_abcdefghijklmnop", status: "running", revision: 1, attempt_number: 1, language: "fr", allowed_actions: ["cancel"], identical_relaunch_available: false, inputs: [], warnings: [], expires_at: null, progress: { stage: "transcription", estimate_status: "available", estimate_seconds: 10, overall_ratio: .4, current_ratio: .2 }, stages: [{ code: "transcription", status: "active", progress: .2 }] };

async function expectNoAccessibilityViolations(page: import("@playwright/test").Page) {
  const { violations } = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(violations.map(({ id, impact, nodes }) => ({
    id,
    impact,
    nodes: nodes.map(node => ({ target: node.target, summary: node.failureSummary })),
  }))).toEqual([]);
}

test("accessibilite-structurelle: progressions, actions et absence d-interactifs-imbriques", async ({ page }) => {
  await page.route("**/api/v1/config/public", r => r.fulfill({ json: config }));
  await page.route("**/events", r => r.fulfill({ status: 503 }));
  await page.route("**/api/v1/jobs/job_abcdefghijklmnop", r => r.fulfill({ json: job }));
  await page.goto(`/jobs/job_abcdefghijklmnop#secret=${secret}`);
  await expect(page.locator(".total-progress progress")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Annuler" })).toBeEnabled();
  await expect(page.locator("button button, a button, button a, a a")).toHaveCount(0);
  await expect(page.locator("[aria-live='polite'], [role='status']")).toHaveCount(0);
  await expectNoAccessibilityViolations(page);
});

test("accessibilite-axe: creation et aide integree", async ({ page }) => {
  await page.route("**/api/v1/config/public", r => r.fulfill({ json: config }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Préparer une session Tara" })).toBeVisible();
  await expectNoAccessibilityViolations(page);
  await page.getByRole("button", { name: "Aide" }).click();
  await expect(page.getByRole("heading", { name: "Utiliser Tara en toute sécurité" })).toBeVisible();
  await expectNoAccessibilityViolations(page);
});
