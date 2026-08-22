import { type ChangeEvent, useCallback, useEffect, useReducer, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError, publicErrorMessage, type PublicConfig, type SessionSnapshot } from "../api/client";
import { clearPending, getPending, setPending, type PendingUpload } from "../features/upload/pending";
import { hashFile } from "../features/upload/hashFile";
import { runBounded } from "../features/upload/queue";
import { withSecret } from "../routing/secret";

type ServerFile = SessionSnapshot["files"][number];

const chunkHash = async (blob: Blob) => Array.from(
  new Uint8Array(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer())),
).map((value) => value.toString(16).padStart(2, "0")).join("");

const isAbort = (reason: unknown) => reason instanceof DOMException && reason.name === "AbortError";
const personFor = (file: File) => file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim() || file.name;
const inputKindFor = (snapshot: SessionSnapshot | null, pending: PendingUpload[]) => snapshot?.input_type ?? pending[0]?.inputKind ?? "audio";

export function UploadSessionPage({
  sessionId,
  secret,
  config,
  go,
  embedded = false,
  autoLaunch = true,
  pendingRevision = 0,
  onSnapshot,
  onPendingChange,
}: {
  sessionId: string;
  secret?: string;
  config: PublicConfig;
  go: (path: string) => void;
  embedded?: boolean;
  autoLaunch?: boolean;
  pendingRevision?: number;
  onSnapshot?: (snapshot: SessionSnapshot) => void;
  onPendingChange?: (pending: PendingUpload[]) => void;
}) {
  const { t } = useTranslation();
  const apiError = (reason: unknown) => publicErrorMessage(reason, t("errors.generic"), (code) => t("errors.support_code", { code }));
  const initial = getPending(sessionId);
  const [snapshot, setSnapshot] = useState<SessionSnapshot | null>(null);
  const snapshotRef = useRef<SessionSnapshot | null>(null);
  const [pending, setPendingState] = useState<PendingUpload[]>(initial);
  const pendingRef = useRef(initial);
  const mounted = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const cancelling = useRef(false);
  const launching = useRef(false);
  const [queueRevision, bumpQueue] = useReducer((value: number) => value + 1, 0);
  const [fatalError, setFatalError] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [zipPeople, setZipPeople] = useState<Record<string, string>>({});

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const commit = useCallback((next: PendingUpload[]) => {
    pendingRef.current = next;
    setPending(sessionId, next);
    onPendingChange?.(next);
    if (mounted.current) setPendingState(next);
  }, [onPendingChange, sessionId]);

  const patch = useCallback((key: string, change: Partial<PendingUpload>) => {
    commit(pendingRef.current.map((item) => item.key === key ? { ...item, ...change } : item));
  }, [commit]);

  const refresh = useCallback(async () => {
    if (!secret) return;
    try {
      const value = await api.getSession(sessionId, secret);
      snapshotRef.current = value;
      setSnapshot(value);
      onSnapshot?.(value);
      setFatalError(false);
    } catch {
      if (!snapshotRef.current) setFatalError(true);
    }
  }, [onSnapshot, secret, sessionId]);

  useEffect(() => {
    const stored = getPending(sessionId);
    const known = new Set(pendingRef.current.map((item) => item.key));
    const additions = stored.filter((item) => !known.has(item.key));
    if (additions.length) {
      commit([...pendingRef.current, ...additions]);
      if (!controller.current) bumpQueue();
    }
  }, [commit, pendingRevision, sessionId]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 1500);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!secret) return;
    let activeController: AbortController | null = null;
    const startTimer = setTimeout(() => {
      const eligible = pendingRef.current.filter((item) => item.state === "queued");
      if (!eligible.length || controller.current) return;
      activeController = new AbortController();
      controller.current = activeController;

      const upload = async (seed: PendingUpload) => {
        let current = pendingRef.current.find((item) => item.key === seed.key) ?? seed;
        let fileId = current.fileId;
        let fileRevision = current.fileRevision;
        try {
          if (!fileId) {
            patch(seed.key, { state: "hashing", hashingLoaded: 0, error: undefined });
            const sha256 = await hashFile(
              seed.file,
              (loaded) => patch(seed.key, { hashingLoaded: loaded }),
              activeController!.signal,
            );
            patch(seed.key, { state: "declaring" });
            const body = {
              filename: seed.file.name,
              size: seed.file.size,
              sha256,
              mime: seed.file.type || null,
            };
            const declared = seed.replacementFor
              ? await api.replaceFile(
                sessionId,
                secret,
                seed.replacementFor,
                body,
                seed.idempotencyKey,
                activeController!.signal,
              )
              : await api.declareFile(
                sessionId,
                secret,
                body,
                seed.idempotencyKey,
                activeController!.signal,
              );
            fileId = declared.file_id;
            fileRevision = declared.revision;
            patch(seed.key, { fileId, fileRevision, confirmedOffset: 0 });
          }

          const position = await api.fileOffset(sessionId, secret, fileId);
          let offset = position.confirmed_offset;
          fileRevision = position.revision;
          let resyncAttempts = 0;
          patch(seed.key, { state: "uploading", confirmedOffset: offset, fileRevision });
          while (offset < seed.file.size) {
            if (activeController!.signal.aborted) throw new DOMException("Aborted", "AbortError");
            const chunk = seed.file.slice(
              offset,
              Math.min(offset + config.recommended_chunk_bytes, seed.file.size),
            );
            let uploaded;
            try {
              uploaded = await api.uploadChunk(
                sessionId,
                fileId,
                secret,
                offset,
                await chunkHash(chunk),
                chunk,
                activeController!.signal,
              );
            } catch (reason) {
              if (!(reason instanceof ApiError) || reason.status !== 409 || reason.code !== "upload_chunk_conflict" || resyncAttempts >= 3) throw reason;
              const resynced = await api.fileOffset(sessionId, secret, fileId);
              if (resynced.confirmed_offset < 0 || resynced.confirmed_offset > seed.file.size) throw reason;
              offset = resynced.confirmed_offset;
              fileRevision = resynced.revision;
              resyncAttempts += 1;
              patch(seed.key, { confirmedOffset: offset, fileRevision });
              continue;
            }
            offset = uploaded.confirmed_offset;
            fileRevision = uploaded.revision;
            resyncAttempts = 0;
            patch(seed.key, { confirmedOffset: offset, fileRevision });
          }

          patch(seed.key, { state: "finalizing" });
          current = pendingRef.current.find((item) => item.key === seed.key) ?? seed;
          const revision = current.inputKind === "audio"
            ? (await api.changePerson(sessionId, fileId, secret, fileRevision, current.person ?? personFor(current.file))).revision
            : fileRevision;
          await api.finalizeFile(sessionId, fileId, secret, revision);
          patch(seed.key, { state: "done", fileRevision: revision + 1 });
        } catch (reason) {
          if (isAbort(reason)) {
            if (!cancelling.current) patch(seed.key, { state: "queued" });
          } else {
            patch(seed.key, {
              state: "failed",
              error: reason instanceof Error && reason.message === "hashing_unavailable"
                ? t("errors.input_invalid")
                : apiError(reason),
            });
          }
        }
      };

      void runBounded(eligible, config.parallel_uploads, upload, activeController.signal)
        .finally(() => {
          if (controller.current === activeController) controller.current = null;
          void refresh();
          if (mounted.current && pendingRef.current.some((item) => item.state === "queued")) {
            bumpQueue();
          }
        });
    }, 0);

    return () => {
      clearTimeout(startTimer);
      activeController?.abort();
    };
  }, [config.parallel_uploads, config.recommended_chunk_bytes, patch, queueRevision, refresh, secret, sessionId, t]);

  const queueFile = useCallback((item: PendingUpload) => {
    const next = [...pendingRef.current.filter((value) => value.fileId !== item.fileId), item];
    commit(next);
    if (!controller.current) bumpQueue();
  }, [commit]);

  const reselect = useCallback((serverFile: ServerFile, event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size !== serverFile.total_size || (serverFile.display_name && file.name !== serverFile.display_name)) {
      setActionError(t("upload.wrong_file"));
      return;
    }
    setActionError(null);
    const replace = serverFile.allowed_actions.includes("replace_file");
    queueFile({
      key: crypto.randomUUID(),
      file,
      inputKind: inputKindFor(snapshotRef.current, pendingRef.current),
      ...(inputKindFor(snapshotRef.current, pendingRef.current) === "audio" ? { person: serverFile.person ?? personFor(file) } : {}),
      state: "queued",
      hashingLoaded: 0,
      confirmedOffset: replace ? 0 : serverFile.confirmed_offset,
      idempotencyKey: crypto.randomUUID(),
      fileId: replace ? undefined : serverFile.file_id,
      fileRevision: replace ? undefined : serverFile.revision,
      replacementFor: replace ? serverFile.file_id : undefined,
    });
  }, [queueFile, t]);

  const retryLocal = (key: string) => {
    const item = pendingRef.current.find((value) => value.key === key);
    const server = snapshotRef.current?.files.find((file) => file.file_id === item?.fileId);
    if (item?.fileId && item.confirmedOffset >= item.file.size && item.person !== server?.person) {
      void persistPerson(item, server);
      return;
    }
    patch(key, { state: "queued", error: undefined });
    if (!controller.current) bumpQueue();
  };

  const runServerAction = async (action: () => Promise<unknown>) => {
    setActionError(null);
    try {
      await action();
      await refresh();
    } catch (reason) {
      setActionError(apiError(reason));
    }
  };

  async function persistPerson(item: PendingUpload, server?: ServerFile) {
    const person = (item.person ?? "").trim();
    if (!secret || !person || !item.fileId || person === server?.person) return;
    const revision = server?.revision ?? item.fileRevision;
    if (!revision) return;
    patch(item.key, { state: "finalizing", error: undefined });
    try {
      const changed = await api.changePerson(
        sessionId,
        item.fileId,
        secret,
        revision,
        person,
      );
      patch(item.key, { state: "done", fileRevision: changed.revision });
      await refresh();
    } catch (reason) {
      patch(item.key, { state: "failed", error: apiError(reason) });
      await refresh();
    }
  }

  const localBusy = pending.some((item) => ["queued", "hashing", "declaring", "uploading", "finalizing"].includes(item.state));
  const localFailed = pending.some((item) => item.state === "failed");
  const automaticInputKind = inputKindFor(snapshot, pending);
  useEffect(() => {
    if (!autoLaunch || !secret || automaticInputKind === "zip" || !snapshot?.allowed_actions.includes("launch") || localBusy || localFailed || launching.current) return;
    const timer = setTimeout(() => {
      if (launching.current) return;
      launching.current = true;
      void api.launchJob(sessionId, secret, snapshot.revision)
        .then((job) => {
          clearPending(sessionId);
          go(withSecret(`/jobs/${job.job_id}`, secret));
        })
        .catch((reason) => {
          launching.current = false;
          setActionError(apiError(reason));
        });
    }, 0);
    return () => clearTimeout(timer);
  }, [autoLaunch, automaticInputKind, go, localBusy, localFailed, secret, sessionId, snapshot, t]);

  if (!secret || fatalError) {
    const UnavailableWrapper = embedded ? "section" : "main";
    return <UnavailableWrapper className="page"><h1>{t("upload.unavailable")}</h1></UnavailableWrapper>;
  }

  const mappedIds = new Set(pending.flatMap((item) => item.fileId ? [item.fileId] : []));
  const inputKind = inputKindFor(snapshot, pending);
  const pendingMergedFileId = pending.find((item) => item.inputKind === "merged_transcription")?.fileId;
  const mergedFile = snapshot?.files.find((file) => file.file_id === pendingMergedFileId)
    ?? snapshot?.files.find((file) => file.schema_name === "tara.merged_transcription")
    ?? (inputKind === "merged_transcription" ? snapshot?.files[0] : undefined);
  const errorPath = typeof mergedFile?.error?.parameters?.path === "string"
    ? mergedFile.error.parameters.path
    : t("upload.document");
  const standaloneServerFiles = (snapshot?.files ?? []).filter((file) => !mappedIds.has(file.file_id));
  const total = pending.reduce((sum, item) => sum + item.file.size, 0)
    + standaloneServerFiles.reduce((sum, item) => sum + item.total_size, 0);
  const done = pending.reduce((sum, item) => sum + item.confirmedOffset, 0)
    + standaloneServerFiles.reduce((sum, item) => sum + item.confirmed_offset, 0);
  const zipPhases = ["transfer", "extraction", "track_validation", "launch_preparation"] as const;
  const launchZip = async () => {
    if (!secret || !snapshot || launching.current) return;
    const tracks = snapshot.files.filter((file) => file.status === "ready");
    if (!tracks.length || tracks.some((file) => !(zipPeople[file.file_id] ?? file.person ?? "").trim())) {
      setActionError(t("upload.zip_person_required"));
      return;
    }
    launching.current = true;
    setActionError(null);
    try {
      for (const file of tracks) {
        const person = (zipPeople[file.file_id] ?? file.person ?? "").trim();
        if (person !== file.person) await api.changePerson(sessionId, file.file_id, secret, file.revision, person);
      }
      const job = await api.launchJob(sessionId, secret, snapshot.revision);
      clearPending(sessionId);
      go(withSecret(`/jobs/${job.job_id}`, secret));
    } catch (reason) {
      launching.current = false;
      setActionError(apiError(reason));
      await refresh();
    }
  };

  const Wrapper = embedded ? "section" : "main";
  return <Wrapper className={embedded ? "upload-page embedded-upload" : "page upload-page"}>
    {!embedded && <header className="page-header"><p className="eyebrow">{t("upload.eyebrow")}</p><h1>{t("upload.title")}</h1><p className="lede">{t("upload.lede")}</p></header>}
    {inputKind === "merged_transcription" && <section className="validation-details" aria-live="polite">
      <h2>{t("upload.merged_transcription")}</h2>
      {mergedFile?.schema_name && <p>{t("upload.schema_name", { name: mergedFile.schema_name })}</p>}
      {mergedFile?.schema_version && <p>{t("upload.schema_version", { version: mergedFile.schema_version })}</p>}
      {typeof mergedFile?.token_count === "number" && <p>{t("upload.token_count", { count: mergedFile.token_count })}</p>}
      {mergedFile?.error && <p className="error" role="alert">{t(mergedFile.error.message_key as never, { ...mergedFile.error.parameters, path: errorPath, defaultValue: t("upload.validation_error", { path: errorPath }) })}</p>}
    </section>}
    {inputKind === "zip" && <section className="validation-details" aria-live="polite">
      <h2>{t("upload.zip_tracks")}</h2>
      <ol aria-label={t("upload.zip_progress")}>
        {zipPhases.map((phase) => <li key={phase} aria-current={snapshot?.archive_phase === phase ? "step" : undefined}>{t(`upload.zip_phase.${phase}` as never)}</li>)}
      </ol>
      <p>{t("upload.zip_excluded", { count: snapshot?.archive_excluded_count ?? 0 })}</p>
    </section>}
    <progress value={done} max={Math.max(total, 1)} aria-label={t("upload.global_progress")} />
    {actionError && <p className="error" role="alert">{actionError}</p>}
    <ul className="upload-list">
      {pending.map((item) => {
        const server = snapshot?.files.find((file) => file.file_id === item.fileId);
        const state = server?.status ?? item.state;
        const confirmedOffset = server?.confirmed_offset ?? item.confirmedOffset;
        return <li key={item.key}>
        <div className="upload-file-heading">
          <strong>{item.file.name}</strong>
          <span>{t(`upload.state.${state}` as never, { defaultValue: state })}</span>
        </div>
        {inputKind === "audio" && <label>{t("upload.person")}
          <input
            value={item.person}
            disabled={item.state === "finalizing"}
            onChange={(event) => patch(item.key, { person: event.target.value })}
            onBlur={() => { if (item.state === "done") void persistPerson(item, server); }}
          />
        </label>}
        {item.state === "hashing" && <progress value={item.hashingLoaded} max={Math.max(item.file.size, 1)} aria-label={t("upload.hashing_progress")} />}
        {item.state !== "hashing" && <progress value={confirmedOffset} max={Math.max(item.file.size, 1)} aria-label={t("upload.file_progress", { name: item.file.name })} />}
        <span>{Math.round(confirmedOffset / Math.max(item.file.size, 1) * 100)} %</span>
        {item.error && <p className="error">{item.error}</p>}
        {item.state === "failed" && <button type="button" onClick={() => retryLocal(item.key)}>{t("upload.retry")}</button>}
      </li>;
      })}
      {standaloneServerFiles.map((file) => {
        const canResume = file.confirmed_offset < file.total_size && ["created", "uploading"].includes(file.status);
        const canReplace = file.allowed_actions.includes("replace_file");
        return <li key={file.file_id}>
          <div className="upload-file-heading">
            <strong>{file.archive_entry_name ?? file.display_name ?? file.file_id}</strong>
            <span>{t(`upload.state.${file.status}` as never, { defaultValue: file.status })}</span>
          </div>
          {inputKind === "zip" && file.status === "ready" && <label>{t("upload.person")}
            <input
              value={zipPeople[file.file_id] ?? file.person ?? ""}
              onChange={(event) => setZipPeople((current) => ({ ...current, [file.file_id]: event.target.value }))}
            />
          </label>}
          <progress value={file.confirmed_offset} max={Math.max(file.total_size, 1)} aria-label={t("upload.file_progress", { name: file.display_name ?? file.file_id })} />
          <span>{Math.round(file.confirmed_offset / Math.max(file.total_size, 1) * 100)} %</span>
          {(canResume || canReplace) && <label className="file-text">{t(canReplace ? "upload.replace" : "upload.resume")}
            <input type="file" accept={inputKind === "merged_transcription" ? ".yaml,.yml,application/yaml,text/yaml" : inputKind === "zip" ? ".zip,application/zip" : "audio/mpeg,audio/ogg,.mp3,.ogg"} onChange={(event) => reselect(file, event)} />
          </label>}
          {file.allowed_actions.includes("retry_finalization") && <button type="button" onClick={() => void runServerAction(() => api.retryFile(sessionId, file.file_id, secret, file.revision ?? 1))}>{t("upload.retry")}</button>}
          {file.allowed_actions.includes("delete_file") && <button type="button" onClick={() => void runServerAction(() => api.deleteFile(sessionId, file.file_id, secret, file.revision ?? 1))}>{t("upload.delete")}</button>}
        </li>;
      })}
    </ul>
    {inputKind === "zip" && snapshot?.allowed_actions.includes("launch") && <button type="button" className="primary" disabled={launching.current} onClick={() => void launchZip()}>{t("upload.zip_confirm")}</button>}
    {!embedded && snapshot?.allowed_actions.includes("cancel") && <button type="button" className="danger" onClick={() => {
      cancelling.current = true;
      controller.current?.abort();
      setActionError(null);
      void api.cancelSession(sessionId, secret, snapshot.revision)
        .then(async () => {
          clearPending(sessionId);
          pendingRef.current = [];
          setPendingState([]);
          await refresh();
        })
        .catch((reason) => setActionError(apiError(reason)));
    }}>{t("upload.cancel")}</button>}
  </Wrapper>;
}
