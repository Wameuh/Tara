import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PublicBlockRenderer } from "./PublicBlockRenderer";
describe("public blocks", () => { it("renders safe V1 blocks and ignores unknown and javascript URLs", () => { render(<PublicBlockRenderer query="" blocks={[{ type: "paragraph", text: "<img src=x onerror=alert(1)>" }, { type: "list", items: ["one"] }, { type: "orderedList", items: ["two"] }, { type: "keyValue", entries: [{ key: "k", value: "v" }] }, { type: "table", headers: ["h"], rows: [["c"]] }, { type: "callout", title: "n", text: "t" }, { type: "paragraph", text: "l", links: [{ label: "bad", href: "javascript:alert(1)" }] }, { type: "unknown" }]} />); expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument(); expect(screen.queryByRole("link", { name: "bad" })).toBeNull(); expect(screen.getByRole("table")).toBeInTheDocument(); }); });
