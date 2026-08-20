import { describe, expect, it } from "vitest";

import { catalogues } from "./index";

describe("catalogues", () => {
  it("keeps the French default catalogue complete", () => {
    expect(Object.keys(catalogues.fr).sort()).toEqual(Object.keys(catalogues.en).sort());
    expect(catalogues.fr["app.title"]).toBe("Tara");
  });
});
