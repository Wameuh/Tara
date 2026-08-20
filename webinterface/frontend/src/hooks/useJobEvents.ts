import { useEffect } from "react";
import { streamEvents, type EventEnvelope } from "../api/sse";

function abortableDelay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}

export function useJobEvents(
  jobId: string,
  secret: string | undefined,
  onEvent: (event: EventEnvelope) => void,
): void {
  useEffect(() => {
    if (!secret) return;
    const controller = new AbortController();
    let retry = 250;
    const connect = async () => {
      while (!controller.signal.aborted) {
        try {
          await streamEvents(`/api/v1/jobs/${jobId}/events`, secret, onEvent, controller.signal);
          retry = 250;
        } catch {
          retry = Math.min(retry * 2, 5000);
        }
        await abortableDelay(retry, controller.signal);
      }
    };
    void connect();
    return () => controller.abort();
  }, [jobId, secret, onEvent]);
}
