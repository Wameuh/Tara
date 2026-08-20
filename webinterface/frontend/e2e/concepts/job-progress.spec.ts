import { expect, test } from "@playwright/test";

const secret = "s".repeat(43);
const config = { language: "fr", locale: "fr-FR", supported_languages: ["fr"], max_upload_bytes: 1024, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 };
const job = (status: string, revision = 1) => ({
  job_id: "job_abcdefghijklmnop", status, revision, attempt_number: 1, language: "fr",
  allowed_actions: status === "running" ? ["cancel", "regenerate_secret"] : [], identical_relaunch_available: false,
  inputs: [], warnings: [], expires_at: "2026-07-24T12:00:00Z", started_at: "2026-07-17T12:00:00Z",
  progress: { overall_ratio: 0.42, current_ratio: 0.75, stage: "transcription", substage_code: "working", estimate_status: "available", estimate_seconds: 42 },
  stages: [{ code: "input_validation", status: "completed", progress: 1 }, { code: "transcription", status: "active", progress: 0.75 }],
});

async function mockJobApi(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/config/public", route => route.fulfill({ json: config }));
  await page.route("**/events", route => route.fulfill({ status: 503, body: "offline" }));
  await page.route("**/api/v1/jobs/job_abcdefghijklmnop", route => route.fulfill({ json: job("running") }));
}

test("suivi: deux progressions, annulation et fallback SSE", async ({ page }) => {
  await mockJobApi(page);
  await page.goto(`/jobs/job_abcdefghijklmnop#secret=${secret}`);
  await expect(page.getByRole("heading", { name: "Analyse en cours" })).toBeVisible();
  await expect(page.locator(".total-progress progress")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Annuler" })).toBeVisible();
  await expect(page.locator("[role='status']")).toHaveCount(0);
  await expect(page.locator("button button")).toHaveCount(0);
});

test("suivi: les quatre viewports restent sans debordement horizontal", async ({ page }) => {
  await mockJobApi(page);
  for (const viewport of [{ width: 1440, height: 900 }, { width: 980, height: 900 }, { width: 390, height: 844 }, { width: 320, height: 720 }]) {
    await page.setViewportSize(viewport);
    await page.goto(`/jobs/job_abcdefghijklmnop#secret=${secret}`);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(page).toHaveScreenshot(`job-progress-${viewport.width}.png`, { fullPage: true, animations: "disabled", maxDiffPixelRatio: 0.01 });
  }
});
