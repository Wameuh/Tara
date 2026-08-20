import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const forbidden = [
  /(?:^|[^a-z])\/data\//i,
  /\/home\//i,
  /\/config\//i,
  /C:\\Users\\/i,
  /TARA_WEB_/i,
  /sqlite_path/i,
  /backups_root/i,
  /link_secret/i,
];

function filesUnder(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? filesUnder(path) : [path];
  });
}

export function scanBundle(distDir) {
  const assetsDir = join(distDir, "assets");
  const files = filesUnder(assetsDir);
  if (!files.some((file) => /^tara-logo-black-[\w-]+\.png$/.test(file.split(/[\\/]/).pop()))) throw new Error("missing fingerprinted black logo");
  if (files.some((file) => /white|gallery/i.test(file))) throw new Error("forbidden brand asset in runtime bundle");
  const text = [readFileSync(join(distDir, "index.html"), "utf8"), ...files.filter((file) => /\.(js|css)$/.test(file)).map((file) => readFileSync(file, "utf8"))].join("\n").replaceAll("/api/v1/config/public", "");
  if (forbidden.some((pattern) => pattern.test(text))) throw new Error("server-only value in bundle");
}

if (globalThis.process.argv[1] === fileURLToPath(import.meta.url)) scanBundle("dist");
