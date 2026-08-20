import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";

const fixture = path.resolve("e2e/fixtures/tone.ogg");

test("scenario-01-reel-compose: creation multi-audios, upload, suivi et resultat", async ({ page, browser }) => {
  test.skip(!process.env.E2E_BASE_URL, "requires the Docker Compose proxy");
  const audio = await readFile(fixture);
  const requestUrls: string[] = [];
  const privateValues = ["Contexte de validation prive.", "Resume precedent prive."];
  page.on("request", request => requestUrls.push(request.url()));
  await page.goto("/");
  await page.locator("input[type=file]").first().setInputFiles([
    { name: "alice.ogg", mimeType: "audio/ogg", buffer: audio },
    { name: "bob.ogg", mimeType: "audio/ogg", buffer: audio },
  ]);
  await page.locator("textarea").nth(0).fill("Contexte de validation prive.");
  await page.locator("textarea").nth(1).fill("Resume precedent prive.");
  await page.getByRole("textbox", { name: "Personne" }).first().fill("Alice validee");
  await page.getByRole("button", { name: /Lancer/ }).click();
  await expect(page).toHaveURL(/\/sessions\//, { timeout: 15_000 });
  await expect(page).toHaveURL(/#secret=/);
  await expect(page).toHaveURL(/\/jobs\//, { timeout: 30_000 });
  await expect(page.locator("main")).toContainText(
    /Résultat disponible|Analyse en cours|Analyse Tara terminée/i,
    { timeout: 30_000 },
  );
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect(requestUrls.every(url => !url.includes("secret=") && privateValues.every(value => !url.includes(encodeURIComponent(value))))).toBeTruthy();
  const shared = page.url();
  await page.reload();
  await expect(page).toHaveURL(/#secret=/);
  await expect(page.locator("main")).toContainText(/Analyse Tara|R.sultat/, { timeout: 15_000 });
  const second = await browser.newContext();
  const reopened = await second.newPage();
  const reopenedRequests: string[] = [];
  reopened.on("request", request => reopenedRequests.push(request.url()));
  await reopened.goto(shared);
  await expect(reopened.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 15_000 });
  expect(reopenedRequests.every(url => !url.includes("secret="))).toBeTruthy();
  await second.close();
});
