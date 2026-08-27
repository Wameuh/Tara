import { describe, expect, it } from "vitest";

import { safeLink } from "./safeLink";

describe("safeLink", () => {
  it("accepts only explicit absolute HTTP(S) URLs", () => {
    expect(safeLink("https://ko-fi.com/tara")).toBe("https://ko-fi.com/tara");
    expect(safeLink("http://example.com/path")).toBe("http://example.com/path");
    expect(safeLink("//evil.example/path")).toBeNull();
    expect(safeLink("/relative")).toBeNull();
    expect(safeLink("javascript:alert(1)")).toBeNull();
    expect(safeLink("data:text/html,evil")).toBeNull();
  });
});
