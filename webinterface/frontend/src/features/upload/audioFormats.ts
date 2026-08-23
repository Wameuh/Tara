export const AUDIO_FORMATS = "MP3, OGG, AAC, M4A";
const mimesByExtension = new Map<string, Set<string>>([
  ["mp3", new Set(["audio/mpeg", "audio/mp3"])],
  ["ogg", new Set(["audio/ogg", "application/ogg"])],
  ["aac", new Set(["audio/aac", "audio/aacp", "audio/x-aac"])],
  ["m4a", new Set(["audio/mp4", "audio/m4a", "audio/x-m4a"])],
]);

export const AUDIO_ACCEPT = [
  "audio/mpeg",
  "audio/ogg",
  "audio/aac",
  "audio/aacp",
  "audio/x-aac",
  "audio/mp4",
  "audio/m4a",
  "audio/x-m4a",
  ".mp3",
  ".ogg",
  ".aac",
  ".m4a",
].join(",");

export function suppliedAudioFormat(file: File): string {
  const match = /\.([^.]+)$/.exec(file.name);
  const extension = match?.[1]?.toUpperCase();
  return [extension, file.type || null].filter(Boolean).join(" · ") || "?";
}

export function acceptsAudioFile(file: File): boolean {
  const match = /\.([^.]+)$/.exec(file.name);
  const extension = match?.[1]?.toLowerCase();
  const mimes = extension ? mimesByExtension.get(extension) : undefined;
  return Boolean(mimes && (!file.type || mimes.has(file.type.toLowerCase())));
}
