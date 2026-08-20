import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import "../../../i18n";
import type { ResultSnapshot } from "../../../api/client";
import { ResultHeader } from "./ResultHeader";

const result = {
  type: "tara_result_v1",
  status: "available",
  expires_at: "2030-01-01T00:00:00Z",
  sections: [],
  cost: {
    status: "partial",
    value_micro_eur: 1_234_567,
    explanation_key: "result.cost_partial_explanation",
  },
} satisfies ResultSnapshot;

describe("ResultHeader", () => {
  it("formats five decimals and explains a partial cost accessibly", () => {
    render(<ResultHeader result={result} locale="fr-FR" />);
    expect(screen.getByText(/1,23457/)).toBeInTheDocument();
    const information = screen.getByRole("button", { name: "Informations sur le coût" });
    expect(information).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(information);
    expect(information).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Total partiel");
  });
});
