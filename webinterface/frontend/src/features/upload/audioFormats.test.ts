import { describe, expect, it } from "vitest";

import { acceptsAudioFile, suppliedAudioFormat } from "./audioFormats";

describe("audio upload formats", () => {
  it.each([
    ["track.mp3", "audio/mpeg"],
    ["track.ogg", "audio/ogg"],
    ["track.aac", "audio/aac"],
    ["track.m4a", "audio/mp4"],
  ])("accepts %s with %s", (name, type) => {
    expect(acceptsAudioFile(new File(["audio"], name, { type }))).toBe(true);
  });

  it.each([
    ["track.wav", "audio/wav"],
    ["track.mp3", "audio/ogg"],
    ["track", "audio/aac"],
  ])("rejects %s with %s", (name, type) => {
    expect(acceptsAudioFile(new File(["audio"], name, { type }))).toBe(false);
  });

  it("reports both the supplied extension and MIME type", () => {
    expect(suppliedAudioFormat(new File(["audio"], "track.wav", { type: "audio/wav" })))
      .toBe("WAV · audio/wav");
  });
});
