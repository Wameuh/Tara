import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ResultSnapshot } from "../api/client";
import "../i18n";
import { ResultPage } from "./JobPage";

const result: ResultSnapshot = {
  type: "tara_result_v1",
  status: "available",
  expires_at: "2030-01-01T00:00:00Z",
  summary_markdown: null,
  cost: {
    status: "unavailable",
    value_micro_eur: null,
    explanation_key: "result.cost_unavailable_explanation",
  },
  sections: [],
};

describe("completed result capability", () => {
  afterEach(cleanup);

  it("keeps the owner-link bearer warning visible", () => {
    render(
      <ResultPage
        result={result}
        locale="fr-FR"
        jobId="job_abcdefghijklmnop"
        secret="abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMN123456789"
        toast={null}
      />,
    );
    expect(
      screen.getByText(/Ce lien équivaut à un accès propriétaire/),
    ).toBeInTheDocument();
  });

  it("keeps the owner-link bearer warning visible after expiry", () => {
    render(
      <ResultPage
        result={{ ...result, status: "expired" }}
        locale="fr-FR"
        jobId="job_abcdefghijklmnop"
        secret="abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMN123456789"
        toast={null}
      />,
    );
    expect(
      screen.getByText(/Ce lien équivaut à un accès propriétaire/),
    ).toBeInTheDocument();
  });
});
