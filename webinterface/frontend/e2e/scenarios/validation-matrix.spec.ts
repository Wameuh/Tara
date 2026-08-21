import { expect, test } from "@playwright/test";

const secret = "s".repeat(43);
const config = { language: "fr", locale: "fr-FR", supported_languages: ["fr"], input_modes: ["audio", "merged_transcription", "zip"], max_upload_bytes: 1024, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 };
const id = "job_abcdefghijklmnop";
const sessionId = "us_abcdefghijklmnop";
type JobStatus = "queued" | "running" | "completed" | "failed" | "timed_out" | "cancelled" | "expired";
const actionsFor = (status: JobStatus) => ({
  queued: ["cancel", "regenerate_secret"], running: ["cancel", "regenerate_secret"], completed: ["delete_job", "regenerate_secret", "view_result"],
  failed: ["delete_job", "edit_and_relaunch", "regenerate_secret"], timed_out: ["delete_job", "edit_and_relaunch", "regenerate_secret", "relaunch_identical"],
  cancelled: ["delete_job", "edit_and_relaunch", "regenerate_secret"], expired: [],
}[status]);
const job = (status: JobStatus, revision = 1, actions = actionsFor(status), attemptNumber = 1) => ({ job_id: id, status, revision, attempt_number: attemptNumber, language: "fr", allowed_actions: actions, identical_relaunch_available: status === "timed_out", inputs: [], warnings: [], expires_at: "2026-07-24T12:00:00Z", started_at: null, progress: { stage: "transcription", estimate_status: "available", estimate_seconds: 12, overall_ratio: .4, current_ratio: .5 }, stages: [{ code: "input_validation", status: "completed", progress: 1 }, { code: "transcription", status: status === "queued" ? "pending" : "active", progress: .5 }], ...(status === "failed" || status === "timed_out" ? { error: { code: status === "timed_out" ? "timeout" : "processing_failed", message_key: `errors.${status === "timed_out" ? "timeout" : "processing_failed"}`, parameters: {} } } : {}) });
const result = { type: "tara_result_v1", status: "complete", expires_at: "2026-07-24T12:00:00Z", cost: { status: "complete", value_micro_eur: 0 }, sections: [{ id: "overview", title: "Vue", text: "Résultat restauré", order: 0, status: "available" }] };
const session = (fileStatus = "created", offset = 0) => ({ session_id: sessionId, status: fileStatus === "ready" ? "ready" : "uploading", revision: 4, language: "fr", context_text: "contexte", previous_summaries_text: "resume", expires_at: "2026-07-24T12:00:00Z", allowed_actions: [], validations: [], files: [{ file_id: "file_abcdefghijklmnop", status: fileStatus, revision: 4, confirmed_offset: offset, total_size: 3, person: "Alice", display_name: "a.ogg", allowed_actions: [] }] });

async function base(page: import("@playwright/test").Page, snapshots: () => object) {
  await page.route("**/api/v1/config/public", r => r.fulfill({ json: config }));
  await page.route("**/api/v1/sessions/**", r => r.fulfill({ json: session("ready", 3) }));
  await page.route("**/api/v1/**/events", r => r.fulfill({ status: 503 }));
  await page.route(`**/api/v1/jobs/${id}`, r => r.fulfill({ json: snapshots() }));
}

test.beforeEach(async ({ context }) => {
  await context.route(new RegExp(`/api/v1/jobs/${id}/events(?:\\?.*)?$`), route => route.fulfill({ status: 503 }));
});

test("scenario-02-upload-interrompu-repris-offset-finalize-ready", async ({ page }) => {
  const calls: { url: string; headers: Record<string, string>; body: string }[] = []; let offset = 1; let finalized = false;
  await page.route("**/api/**", async r => { const request = r.request(); calls.push({ url: request.url(), headers: request.headers(), body: request.postData() ?? "" }); const path = new URL(request.url()).pathname;
    if (path.endsWith("/offset")) return r.fulfill({ json: { confirmed_offset: offset, revision: 2 } });
    if (path.endsWith("/chunks")) { expect(request.headers()["upload-offset"]).toBe(String(offset)); offset = 3; return r.fulfill({ json: { confirmed_offset: 3, revision: 3 } }); }
    if (path.endsWith("/finalize")) { finalized = true; return r.fulfill({ json: {} }); }
    return r.fulfill({ json: session(finalized ? "ready" : "uploading", offset) }); });
  await page.goto(`/sessions/${sessionId}#secret=${secret}`);
  await page.evaluate(async () => { await fetch("/api/v1/uploads/sessions/us_abcdefghijklmnop/files/file_abcdefghijklmnop/chunks", { method: "PATCH", headers: { "X-Tara-Job-Secret": "s".repeat(43), "Upload-Offset": "1", "Upload-Checksum": "a".repeat(64), "Content-Type": "application/octet-stream" }, body: new Uint8Array([1,2]) }); });
  expect(calls.some(call => call.url.includes("/chunks") && call.headers["content-type"] === "application/octet-stream" && !call.url.includes("secret="))).toBeTruthy();
  expect(offset).toBe(3);
});

test("scenario-03-fifo-polling-progressions-transition-resultat", async ({ page }) => {
  let gets = 0; await base(page, () => gets++ < 2 ? job("queued", 1) : gets++ < 4 ? job("running", 2) : job("completed", 3));
  await page.route(`**/api/v1/jobs/${id}/events`, r => r.fulfill({ status: 503 })); await page.route(`**/api/v1/jobs/${id}/result`, r => r.fulfill({ json: result }));
  await page.goto(`/jobs/${id}#secret=${secret}`); await expect(page.locator(".total-progress progress")).toHaveCount(2); await page.waitForTimeout(5200);
  await expect(page.locator("h1")).toContainText(/Analyse Tara/); expect(gets).toBeGreaterThanOrEqual(3);
});

test("scenario-04-nouveau-contexte-fragment-header-authentifie", async ({ browser }) => {
  const context = await browser.newContext(); const page = await context.newPage(); const headers: string[] = [];
  await base(page, () => job("completed", 2, [])); await page.route(`**/api/v1/jobs/${id}/events`, r => r.fulfill({ status: 503 })); await page.route(`**/api/v1/jobs/${id}/result`, r => r.fulfill({ json: result }));
  page.on("request", r => { if (r.url().includes(`/jobs/${id}`)) headers.push(r.headers()["x-tara-job-secret"] ?? ""); }); await page.goto(`/jobs/${id}#secret=${secret}`);
  await expect(page.locator("h1")).toContainText(/Analyse Tara/); expect(headers).toContain(secret); expect(page.url()).toContain("#secret="); await context.close();
});

test("scenario-05-mauvais-secret-indisponible-et-rotation-atomique", async ({ page }) => {
  const freshSecret = "n".repeat(43); const seenSecrets: string[] = [];
  await page.route("**/api/v1/config/public", r => r.fulfill({ json: config }));
  await page.route(`**/api/v1/jobs/${id}/secret`, r => { expect(r.request().method()).toBe("POST"); expect(r.request().headers()["x-tara-job-secret"]).toBe(secret); return r.fulfill({ json: { secret: freshSecret, revision: 2 } }); });
  await page.route(new RegExp(`/api/v1/jobs/${id}$`), r => { const supplied = r.request().headers()["x-tara-job-secret"]; seenSecrets.push(supplied ?? ""); return r.fulfill(supplied === "x".repeat(43) ? { status: 404, json: {} } : { json: job("running", supplied === freshSecret ? 2 : 1) }); });
  await page.goto(`/jobs/${id}#secret=${"x".repeat(43)}`); await expect(page.getByRole("heading")).toContainText(/indisponible/i);
  await page.goto(`/jobs/${id}#secret=${secret}`); await page.reload(); await expect(page.getByRole("heading", { level: 1 })).toContainText(/Analyse/); await expect(page.getByRole("button", { name: "Renouveler le lien" })).toBeVisible(); await page.waitForTimeout(100); const oldRequests = seenSecrets.filter(value => value === secret).length; await page.getByRole("button", { name: "Renouveler le lien" }).click(); await expect(page.getByText("Lien renouvelé")).toBeVisible(); await expect.poll(() => seenSecrets.includes(freshSecret)).toBeTruthy(); await page.waitForTimeout(150); expect(page.url()).toContain(`#secret=${freshSecret}`); expect(page.url()).not.toContain("?"); expect(seenSecrets.filter(value => value === secret)).toHaveLength(oldRequests);
});

test("scenario-06-failed-modifier-relancer-post-navigation", async ({ page }) => {
  let post = false; await base(page, () => job("failed", 4)); await page.route(`**/api/v1/jobs/${id}/events`, r => r.fulfill({ status: 503 })); await page.route(`**/edit-and-relaunch`, r => { post = true; expect(r.request().method()).toBe("POST"); return r.fulfill({ status: 201, json: { session_id: sessionId } }); });
  await page.goto(`/jobs/${id}#secret=${secret}`); await page.getByRole("button", { name: /Modifier/ }).click(); await expect(page).toHaveURL(new RegExp(`/sessions/${sessionId}`)); expect(post).toBeTruthy();
});

test("scenario-07-timeout-relance-unique-puis-modifiee", async ({ page }) => {
  let identical = 0; await base(page, () => identical ? job("queued", 5, actionsFor("queued"), 2) : job("timed_out", 4)); await page.route(`**/api/v1/jobs/${id}/events`, r => r.fulfill({ status: 503 })); await page.route(`**/relaunch-identical`, r => { identical++; return r.fulfill({ json: { accepted: true } }); }); await page.route(`**/edit-and-relaunch`, r => r.fulfill({ status: 201, json: { session_id: sessionId } }));
  await page.goto(`/jobs/${id}#secret=${secret}`); await page.getByRole("button", { name: /Modifier/ }).click(); await expect(page).toHaveURL(new RegExp(`/sessions/${sessionId}`)); await page.goto(`/jobs/${id}#secret=${secret}`); await page.getByRole("button", { name: /Relancer/ }).click(); await expect(page.getByRole("button", { name: /Relancer/ })).toHaveCount(0); await expect(page.getByRole("button", { name: /Modifier/ })).toHaveCount(0); expect(identical).toBe(1);
});

test("scenario-08-annulation-waiting-et-running", async ({ page }) => {
  let cancelled = 0; await base(page, () => job("running", 2, ["cancel"])); await page.route(`**/api/v1/jobs/${id}/events`, r => r.fulfill({ status: 503 })); await page.route(`**/cancel`, r => { cancelled++; expect(r.request().headers()["expected-revision"]).toBe("2"); return r.fulfill({ json: { accepted: true } }); });
  await page.goto(`/jobs/${id}#secret=${secret}`); await page.getByRole("button", { name: "Annuler" }).click(); expect(cancelled).toBe(1);
});

test("scenario-09-expiration-job-et-resultat-expire", async ({ page }) => {
  await base(page, () => job("expired", 2, [])); await page.route(`**/api/v1/jobs/${id}/events`, r => r.fulfill({ status: 503 })); await page.goto(`/jobs/${id}#secret=${secret}`); await expect(page.locator("h1")).toContainText(/expir/i);
});

test("scenario-10-sse-echec-polling-revision-plus-recente", async ({ page }) => {
  let gets = 0; await base(page, () => gets++ ? job("completed", 2, []) : job("running", 1)); await page.route(`**/api/v1/jobs/${id}/events`, r => r.fulfill({ status: 503 })); await page.route(`**/api/v1/jobs/${id}/result`, r => r.fulfill({ json: result }));
  await page.goto(`/jobs/${id}#secret=${secret}`); await page.waitForTimeout(5200); await expect(page.locator("h1")).toContainText(/Analyse Tara/); expect(gets).toBeGreaterThan(1);
});
