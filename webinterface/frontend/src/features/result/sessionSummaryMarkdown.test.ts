import { describe, expect, it } from "vitest";

import type { ResultSnapshot } from "../../api/client";
import { buildSessionSummaryMarkdown } from "./sessionSummaryMarkdown";

describe("buildSessionSummaryMarkdown", () => {
  it("returns the exact published session summary when available", () => {
    const markdown = "# Résumé de session\n\nTexte exact sans nouvelle ligne finale.";
    const result = {
      type: "tara_result_v1",
      status: "available",
      summary_markdown: markdown,
      sections: [],
      cost: {
        status: "unavailable",
        value_micro_eur: null,
        explanation_key: "result.cost_unavailable_explanation",
      },
    } satisfies ResultSnapshot;

    expect(buildSessionSummaryMarkdown(result, "Titre ignoré")).toBe(markdown);
  });

  it("exports ordered public sections and structured blocks as Markdown", () => {
    const result = {
      type: "tara_result_v1",
      status: "available",
      sections: [
        {
          id: "impacts",
          order: 2,
          title: "Impacts pour la suite",
          status: "available",
          section_type: "generic",
          text: "Quête ouverte",
          blocks: [{ type: "list", items: ["Quête ouverte", "Objet perdu"] }],
        },
        {
          id: "overview",
          order: 1,
          title: "Résumé express",
          status: "available",
          section_type: "overview",
          text: "Le groupe arrive en ville.",
          blocks: [{ type: "paragraph", text: "Le groupe arrive en ville." }],
        },
      ],
      cost: {
        status: "unavailable",
        value_micro_eur: null,
        explanation_key: "result.cost_unavailable_explanation",
      },
    } satisfies ResultSnapshot;

    expect(buildSessionSummaryMarkdown(result, "Résumé de session")).toBe(
      "# Résumé de session\n\n## Résumé express\n\nLe groupe arrive en ville.\n\n" +
        "## Impacts pour la suite\n\n- Quête ouverte\n- Objet perdu\n\n",
    );
  });
});
