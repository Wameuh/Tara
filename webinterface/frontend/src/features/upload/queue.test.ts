import { describe, expect, it } from "vitest";
import { runBounded } from "./queue";

describe("bounded upload queue", () => {
  it("never exceeds the configured concurrency", async () => {
    let active = 0;
    let peak = 0;
    await runBounded([1, 2, 3, 4, 5], 2, async () => {
      active += 1;
      peak = Math.max(peak, active);
      await new Promise((resolve) => setTimeout(resolve, 2));
      active -= 1;
    }, new AbortController().signal);
    expect(peak).toBe(2);
  });

  it("does not start further files after cancellation", async () => {
    const controller = new AbortController();
    const started: number[] = [];
    await runBounded([1, 2, 3], 1, async (item) => {
      started.push(item);
      controller.abort();
    }, controller.signal);
    expect(started).toEqual([1]);
  });
});
