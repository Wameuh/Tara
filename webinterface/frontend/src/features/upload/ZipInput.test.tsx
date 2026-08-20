import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import "../../i18n";
import { ZipInput } from "./ZipInput";

describe("ZipInput", () => {
  it("accepts one ZIP and rejects disguised extensions", () => {
    const change = vi.fn();
    render(<ZipInput file={null} onChange={change} />);
    const input = screen.getByLabelText("Archive ZIP audio");
    fireEvent.change(input, { target: { files: [new File(["zip"], "tracks.ZIP", { type: "application/zip" })] } });
    expect(change).toHaveBeenLastCalledWith(expect.objectContaining({ name: "tracks.ZIP" }));
    fireEvent.change(input, { target: { files: [new File(["no"], "tracks.zip.exe")] } });
    expect(change).toHaveBeenLastCalledWith(null);
  });
});
