import { act, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AnimatedTaraLogo } from "./AnimatedTaraLogo";
import { TaraLogo } from "./TaraLogo";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Tara brand", () => {
  it("uses local responsive assets and one accessible name", () => {
    const { getByRole } = render(<TaraLogo variant="black" size="standalone" decorative={false} />);
    const logo = getByRole("img", { name: "Tara" });
    expect(logo.getAttribute("src")).toContain("tara-logo-black");
    expect(logo.getAttribute("srcset")).toContain("2x");
    expect(logo.getAttribute("width")).toBe("132");
    expect(logo.getAttribute("height")).toBe("44");
  });

  it("waits before animating and falls back to the static logo on asset error", () => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
    const { container } = render(<AnimatedTaraLogo variant="black" size="loading" play />);
    expect(container.querySelectorAll("img")).toHaveLength(1);
    act(() => vi.advanceTimersByTime(250));
    expect(container.querySelectorAll("img")).toHaveLength(2);
    fireEvent.error(container.querySelector(".tara-logo-animation__d20")!);
    expect(container.querySelectorAll("img")).toHaveLength(1);
  });
});
