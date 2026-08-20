import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import "../../../i18n";
import { JobActions } from "./JobActions";
const job = { allowed_actions: ["cancel", "relaunch_identical", "edit_and_relaunch", "regenerate_secret"], revision: 1 } as never;
describe("job actions", () => { it("only renders and invokes allowed actions", () => { const cancel = vi.fn(); const rotate = vi.fn(); render(<JobActions job={job} onCancel={cancel} onCopy={vi.fn()} onRelaunch={vi.fn()} onEdit={vi.fn()} onRotate={rotate} />); fireEvent.click(screen.getByRole("button", { name: "Annuler" })); fireEvent.click(screen.getByRole("button", { name: "Renouveler le lien" })); expect(cancel).toHaveBeenCalledOnce(); expect(rotate).toHaveBeenCalledOnce(); }); });
