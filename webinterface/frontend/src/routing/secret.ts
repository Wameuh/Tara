/** Fragments are never sent in HTTP requests, so they are the shareable owner proof. */
export function readSecret(): string | undefined {
  const value = new URLSearchParams(window.location.hash.slice(1)).get("secret");
  return value && /^[A-Za-z0-9_-]{43,512}$/.test(value) ? value : undefined;
}

export function withSecret(path: string, secret: string, section?: string): string {
  const fragment = new URLSearchParams({ secret });
  if (section) fragment.set("section", section);
  return `${path}#${fragment.toString()}`;
}

export function readSection(): string | undefined {
  return new URLSearchParams(window.location.hash.slice(1)).get("section") ?? undefined;
}
