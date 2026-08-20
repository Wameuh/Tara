import { describe, expect, it } from "vitest";
import { readSecret, withSecret } from "./secret";
describe("owner fragments", () => { it("keeps a secret and section across a refreshable link", () => { const secret = "abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMN123456789"; history.replaceState(null, "", withSecret("/jobs/job_abcdefghijklmnop", secret, "summary")); expect(readSecret()).toBe(secret); expect(location.hash).toContain("section=summary"); }); });
