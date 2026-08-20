import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { scanBundle } from "./check-bundle.mjs";

const root = join(process.cwd(), "tmp-bundle-test");

function dist({ html = "clean", js = "clean", css = "clean", logo = true, extra = "" } = {}) {
  rmSync(root, { recursive: true, force: true });
  mkdirSync(join(root, "assets", "nested"), { recursive: true });
  writeFileSync(join(root, "index.html"), html);
  writeFileSync(join(root, "assets", "nested", "app.js"), js);
  writeFileSync(join(root, "assets", "app.css"), css);
  if (logo) writeFileSync(join(root, "assets", "tara-logo-black-a1.png"), "png");
  if (extra) writeFileSync(join(root, "assets", extra), "png");
  return root;
}

afterEach(() => rmSync(root, { recursive: true, force: true }));

describe("scanBundle", () => {
  it("accepts clean output and API public route", () => {
    expect(() => scanBundle(dist({ js: "/api/v1/config/public" }))).not.toThrow();
  });

  it("rejects contamination isolated to index", () => {
    expect(() => scanBundle(dist({ html: "/config/webinterface.yaml" }))).toThrow();
  });

  it("rejects contamination isolated to JavaScript", () => {
    expect(() => scanBundle(dist({ js: "TARA_WEB_VALUE" }))).toThrow();
  });

  it("rejects contamination isolated to CSS", () => {
    expect(() => scanBundle(dist({ css: "/data/private" }))).toThrow();
  });

  it.each(["/home/user", "C:\\Users\\user", "sqlite_path", "backups_root", "link_secret"]) (
    "rejects %s",
    (value) => expect(() => scanBundle(dist({ js: value }))).toThrow(),
  );

  it("rejects missing, white and gallery logos", () => {
    expect(() => scanBundle(dist({ logo: false }))).toThrow();
    expect(() => scanBundle(dist({ extra: "tara-logo-white.png" }))).toThrow();
    expect(() => scanBundle(dist({ extra: "gallery.png" }))).toThrow();
  });
});
