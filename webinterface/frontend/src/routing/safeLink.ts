/** Keep model/config supplied links on explicit absolute HTTP(S) origins. */
export function safeLink(href: string): string | null {
  try {
    const url = new URL(href);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}
