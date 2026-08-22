import { type ChangeEvent, type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, publicErrorMessage, type InputKind, type PublicConfig } from "../api/client";
import { MonthlyFundingPanel } from "../components/MonthlyFundingPanel";
import { MergedTranscriptionInput } from "../features/upload/MergedTranscriptionInput";
import { ZipInput } from "../features/upload/ZipInput";
import { clearPending, getPending, setPending, type PendingUpload } from "../features/upload/pending";
import { withSecret } from "../routing/secret";
import { clearDraft, loadDraft, saveDraft } from "../storage/draft";
import { UploadSessionPage } from "./UploadSessionPage";

type SelectedAudio = { key: string; file: File; person: string };

const personFor = (file: File) => file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim() || file.name;

async function appendTextFiles(event: ChangeEvent<HTMLInputElement>, current: string, maximum: number): Promise<string> {
  const files = Array.from(event.target.files ?? []);
  event.target.value = "";
  let value = current;
  for (const file of files) {
    if (!/\.(txt|md)$/i.test(file.name) || file.size > maximum) throw new Error("invalid_text_file");
    value = [value, await file.text()].filter(Boolean).join("\n\n");
    if (value.length > maximum) throw new Error("text_input_too_large");
  }
  return value;
}

export function NewJobPage({ config, go }: { config: PublicConfig; go: (path: string) => void }) {
  const { t } = useTranslation();
  const apiError = (reason: unknown) => publicErrorMessage(reason, t("errors.generic"), (code) => t("errors.support_code", { code }));
  const draft = loadDraft();
  const [inputKind, setInputKind] = useState<InputKind>(config.input_modes[0]);
  const [files, setFiles] = useState<SelectedAudio[]>([]);
  const [mergedFile, setMergedFile] = useState<File | null>(null);
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [language, setLanguage] = useState(draft?.language ?? config.language);
  const [context, setContext] = useState(draft?.contextText ?? "");
  const [summaries, setSummaries] = useState(draft?.summariesText ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [audioSession, setAudioSession] = useState<{ sessionId: string; secret: string } | null>(null);
  const [audioSnapshot, setAudioSnapshot] = useState<Awaited<ReturnType<typeof api.getSession>> | null>(null);
  const [audioPending, setAudioPending] = useState<PendingUpload[]>([]);
  const [pendingRevision, setPendingRevision] = useState(0);

  useEffect(() => saveDraft({
    version: 1,
    language,
    contextText: context,
    summariesText: summaries,
    fileNames: inputKind === "audio" ? files.map(({ file }) => file.name) : inputKind === "zip" && zipFile ? [zipFile.name] : mergedFile ? [mergedFile.name] : [],
  }), [inputKind, language, context, summaries, files, mergedFile, zipFile]);

  const importText = async (event: ChangeEvent<HTMLInputElement>, current: string, maximum: number, setter: (value: string) => void) => {
    try {
      setter(await appendTextFiles(event, current, maximum));
      setError(null);
    } catch {
      setError(t("new.text_file_invalid"));
    }
  };

  const prepare = async (kind: InputKind, selected: File[], audioFiles: SelectedAudio[] = []) => {
    if (!selected.length) {
      setError(t(kind === "audio" ? "new.file_required" : kind === "zip" ? "new.zip_invalid" : "new.merged_transcription_invalid"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.createUploadSession(kind);
      await api.updateSessionInputs(created.session_id, created.secret, created.revision, { language, context_text: context, previous_summaries_text: summaries });
      setPending(created.session_id, selected.map((file, index) => ({
        key: kind === "audio" ? audioFiles[index].key : crypto.randomUUID(),
        file,
        inputKind: kind,
        ...(kind === "audio" ? { person: audioFiles[index].person } : {}),
        state: "queued",
        hashingLoaded: 0,
        confirmedOffset: 0,
        idempotencyKey: crypto.randomUUID(),
      }) satisfies PendingUpload));
      clearDraft();
      go(withSecret(`/sessions/${created.session_id}`, created.secret));
    } catch (reason) {
      setError(apiError(reason));
      setBusy(false);
    }
  };

  const pendingFor = (audioFiles: SelectedAudio[]): PendingUpload[] => audioFiles.map((item) => ({
    key: item.key,
    file: item.file,
    inputKind: "audio",
    person: item.person,
    state: "queued",
    hashingLoaded: 0,
    confirmedOffset: 0,
    idempotencyKey: crypto.randomUUID(),
  }));

  const startAudioSession = async (audioFiles: SelectedAudio[]) => {
    if (!audioFiles.length) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createUploadSession("audio");
      const pending = pendingFor(audioFiles);
      setPending(created.session_id, pending);
      setAudioPending(pending);
      setAudioSession({ sessionId: created.session_id, secret: created.secret });
      setBusy(false);
    } catch (reason) {
      setError(apiError(reason));
      setBusy(false);
    }
  };

  const selectAudio = (event: ChangeEvent<HTMLInputElement>) => {
    const additions = Array.from(event.target.files ?? []).map((file) => ({ key: crypto.randomUUID(), file, person: personFor(file) }));
    event.target.value = "";
    if (!additions.length) return;
    const selected = [...files, ...additions];
    setFiles(selected);
    if (!audioSession) {
      void startAudioSession(selected);
      return;
    }
    const pending = [...getPending(audioSession.sessionId), ...pendingFor(additions)];
    setPending(audioSession.sessionId, pending);
    setAudioPending(pending);
    setPendingRevision((value) => value + 1);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const selected = inputKind === "audio" ? files.map(({ file }) => file) : inputKind === "zip" ? (zipFile ? [zipFile] : []) : mergedFile ? [mergedFile] : [];
    if (inputKind !== "audio") {
      void prepare(inputKind, selected, files);
      return;
    }
    if (!selected.length) {
      setError(t("new.file_required"));
      return;
    }
    if (!audioSession) {
      await startAudioSession(files);
      return;
    }
    const currentPending = getPending(audioSession.sessionId);
    const transferActive = currentPending.some((item) => ["queued", "hashing", "declaring", "uploading", "finalizing"].includes(item.state));
    const transferFailed = currentPending.some((item) => item.state === "failed");
    if (!audioSnapshot?.allowed_actions.includes("launch") || transferActive || transferFailed) {
      setError(t(transferFailed ? "new.upload_failed" : "new.upload_in_progress"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateSessionInputs(audioSession.sessionId, audioSession.secret, audioSnapshot.revision, { language, context_text: context, previous_summaries_text: summaries });
      const job = await api.launchJob(audioSession.sessionId, audioSession.secret, updated.revision);
      clearPending(audioSession.sessionId);
      clearDraft();
      go(withSecret(`/jobs/${job.job_id}`, audioSession.secret));
    } catch (reason) {
      setError(apiError(reason));
      setBusy(false);
    }
  };

  const transferActive = audioPending.some((item) => ["queued", "hashing", "declaring", "uploading", "finalizing"].includes(item.state));
  const transferFailed = audioPending.some((item) => item.state === "failed");
  const audioReady = Boolean(audioSession && audioSnapshot?.allowed_actions.includes("launch") && !transferActive && !transferFailed);

  return <main className="page form-page"><header className="page-header"><p className="eyebrow">{t("new.eyebrow")}</p><h1>{t("new.title")}</h1><p className="lede">{t("new.lede")}</p></header><MonthlyFundingPanel /><form className="analysis-form" onSubmit={submit}>
    <fieldset className="input-kind" aria-describedby="input-kind-hint"><legend>{t("new.input_kind")}</legend><p id="input-kind-hint" className="muted">{t("new.input_kind_hint")}</p><div className="input-kind-options">
      {config.input_modes.includes("audio") && <label><input type="radio" name="input-kind" value="audio" checked={inputKind === "audio"} disabled={Boolean(audioSession)} onChange={() => { setInputKind("audio"); setError(null); }} />{t("new.audio")}</label>}
      {config.input_modes.includes("merged_transcription") && <label><input type="radio" name="input-kind" value="merged_transcription" checked={inputKind === "merged_transcription"} disabled={Boolean(audioSession)} onChange={() => { setInputKind("merged_transcription"); setError(null); }} />{t("new.merged_transcription")}</label>}
      {config.input_modes.includes("zip") && <label><input type="radio" name="input-kind" value="zip" checked={inputKind === "zip"} disabled={Boolean(audioSession)} onChange={() => { setInputKind("zip"); setError(null); }} />{t("new.zip")}</label>}
    </div></fieldset>
    {inputKind === "audio" ? <><label className="dropzone"><strong>{t("new.audio")}</strong><span>{t("new.audio_hint")}</span><input type="file" accept="audio/mpeg,audio/ogg,.mp3,.ogg" multiple disabled={busy} onChange={selectAudio} /></label>
      {!audioSession && files.length > 0 && <ul className="file-list">{files.map((item) => <li key={item.key}><strong>{item.file.name}</strong></li>)}</ul>}
      {audioSession && <UploadSessionPage sessionId={audioSession.sessionId} secret={audioSession.secret} config={config} go={go} embedded autoLaunch={false} pendingRevision={pendingRevision} onSnapshot={setAudioSnapshot} onPendingChange={setAudioPending} />}</> : inputKind === "zip" ? <ZipInput file={zipFile} onChange={(file) => { setZipFile(file); setError(file ? null : t("new.zip_invalid")); }} /> : <MergedTranscriptionInput file={mergedFile} onChange={(file) => { setMergedFile(file); setError(file ? null : t("new.merged_transcription_invalid")); }} />}
    <section className="memory-section" aria-labelledby="memory-title">
      <header><p className="eyebrow">{t("new.memory_eyebrow")}</p><h2 id="memory-title">{t("new.memory_title")}</h2><p>{t("new.memory_hint")}</p></header>
      <div className="field-grid">
        <label className="context-field"><span>{t("new.context")}</span><small>{t("new.context_hint")}</small><textarea value={context} onChange={(event) => setContext(event.target.value)} rows={6} /></label>
        <label className="context-field"><span>{t("new.summaries")}</span><small>{t("new.summaries_hint")}</small><textarea value={summaries} onChange={(event) => setSummaries(event.target.value)} rows={6} /></label>
      </div>
    </section>
    <div className="form-actions">{config.supported_languages.length > 1 && <label>{t("job.language")}<select value={language} onChange={(event) => setLanguage(event.target.value)}>{config.supported_languages.map((item) => <option key={item}>{item}</option>)}</select></label>}
      <label className="file-text">{t("new.load_context")}<input type="file" multiple accept=".txt,.md,text/plain,text/markdown" onChange={(event) => void importText(event, context, 200_000, setContext)} /></label>
      <label className="file-text">{t("new.load_summaries")}<input type="file" multiple accept=".txt,.md,text/plain,text/markdown" onChange={(event) => void importText(event, summaries, 2_000_000, setSummaries)} /></label>
      <button className="primary" disabled={busy || (inputKind === "audio" && Boolean(audioSession) && !audioReady)}>{t(busy ? "new.preparing" : inputKind === "audio" && audioSession && !audioReady ? "new.uploading" : "new.submit")}</button></div>
    {error && <p className="error" role="alert">{error}</p>}
  </form></main>;
}
