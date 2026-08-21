import { expect, test } from "@playwright/test";

test("logo: dimensions stables et reduced motion", async ({ page }) => {
  await page.route("**/api/v1/config/public", route => route.fulfill({ json: { language: "fr", locale: "fr-FR", supported_languages: ["fr"], input_modes: ["audio", "merged_transcription", "zip"], max_upload_bytes: 1024, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 } }));
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const logo = page.locator(".brand-link img");
  await expect(logo).toBeVisible();
  const box = await logo.boundingBox();
  await page.waitForTimeout(150);
  expect(await logo.boundingBox()).toEqual(box);
});
