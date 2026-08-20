import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import "../../i18n";
import { MergedTranscriptionInput, isMergedTranscriptionFile } from "./MergedTranscriptionInput";

describe("MergedTranscriptionInput", () => {
  it("accepts only one YAML/YML file at the client boundary", () => {
    const change = vi.fn();
    render(<MergedTranscriptionInput file={null} onChange={change} />);
    const input = screen.getByLabelText("Transcription fusionnee");
    fireEvent.change(input, { target: { files: [new File(["schema_name: tara.merged_transcription"], "merged.yml", { type: "application/yaml" })] } });
    expect(change).toHaveBeenLastCalledWith(expect.objectContaining({ name: "merged.yml" }));
    fireEvent.change(input, { target: { files: [new File(["no"], "notes.txt", { type: "text/plain" })] } });
    expect(change).toHaveBeenLastCalledWith(null);
  });

  it("uses a case-insensitive extension allowlist", () => {
    expect(isMergedTranscriptionFile(new File([], "session.YAML"))).toBe(true);
    expect(isMergedTranscriptionFile(new File([], "session.yml"))).toBe(true);
    expect(isMergedTranscriptionFile(new File([], "session.yaml.exe"))).toBe(false);
  });
});
