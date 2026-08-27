import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SessionSummaryMarkdown from "./SessionSummaryMarkdown";

describe("SessionSummaryMarkdown", () => {
  it("renders common Markdown and GFM structures as semantic HTML", () => {
    const { container } = render(<SessionSummaryMarkdown label="Résumé de session" markdown={`# La séance

## Événements

- Arrivée en ville
- Rencontre avec **Mira**

| Personnage | État |
| --- | --- |
| Mira | Présente |

> Une piste importante.

\`indice\``} />);

    expect(screen.getByRole("region", { name: "Résumé de session" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "La séance" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Événements" })).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("Mira", { selector: "strong" })).toBeInTheDocument();
    expect(container.querySelector("blockquote")).toHaveTextContent("Une piste importante.");
    expect(container.querySelector("code")).toHaveTextContent("indice");
  });

  it("does not render raw HTML, remote images, or unsafe link targets", () => {
    const { container } = render(<SessionSummaryMarkdown label="Résumé" markdown={`Texte sûr.

<script>alert("non")</script>

![pixel](https://example.invalid/tracker.png)

[lien dangereux](javascript:alert(1))`} />);

    expect(container.querySelector("script")).not.toBeInTheDocument();
    expect(container.querySelector("img")).not.toBeInTheDocument();
    expect(container.querySelector("a")).not.toBeInTheDocument();
    expect(screen.getByText("lien dangereux")).toBeInTheDocument();
  });

  it("drops relative and protocol-relative links but keeps absolute HTTPS", () => {
    const { container } = render(<SessionSummaryMarkdown label="Résumé" markdown={`[relatif](/admin)

[protocole](//evil.example/path)

[sûr](https://example.com/path)`} />);

    expect(screen.getByText("relatif").closest("a")).toBeNull();
    expect(screen.getByText("protocole").closest("a")).toBeNull();
    expect(screen.getByRole("link", { name: "sûr" })).toHaveAttribute(
      "href",
      "https://example.com/path",
    );
    expect(container.querySelectorAll("a")).toHaveLength(1);
  });
});
