import { useCallback, useEffect, useRef, useState } from "react";
import { api, type JobSnapshot } from "../../../api/client";
import { useJobEvents } from "../../../hooks/useJobEvents";

export function useJobSnapshot(jobId: string, secret?: string) {
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [disconnected, setDisconnected] = useState(false);
  const revision = useRef(-1);
  const hasSnapshot = useRef(false);

  const refresh = useCallback(async () => {
    if (!secret) return;
    try {
      const value = await api.getJob(jobId, secret);
      if (value.revision >= revision.current) {
        revision.current = value.revision;
        hasSnapshot.current = true;
        setJob(value);
      }
      setError(null);
      setDisconnected(false);
    } catch {
      if (hasSnapshot.current) setDisconnected(true);
      else setError("unavailable");
    }
  }, [jobId, secret]);

  const onEvent = useCallback((event: { revision: number }) => {
    if (event.revision > revision.current) void refresh();
  }, [refresh]);
  useJobEvents(jobId, secret, onEvent);
  useEffect(() => {
    revision.current = -1;
    hasSnapshot.current = false;
    setJob(null);
    setError(null);
    setDisconnected(false);
  }, [jobId, secret]);
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);
  return { job, error, disconnected, refresh };
}
