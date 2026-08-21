import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import "../../../i18n";
import { StageTimeline } from "./StageTimeline";

describe("stage timeline", () => {
  it("identifies the active stage and exposes its progress", () => {
    const job = {
      progress: { substage_code: null },
      stages: [
        { code: "input_validation", status: "completed", progress: null },
        { code: "transcription", status: "active", progress: 0.4 },
        { code: "session_preparation", status: "pending", progress: null },
      ],
    } as never;

    render(<StageTimeline job={job} />);

    expect(screen.getByText("Transcription").closest("li")).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(screen.getByRole("progressbar")).toHaveAttribute("value", "0.4");
  });
});
