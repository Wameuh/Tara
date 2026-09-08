import { type ChangeEvent, type DragEvent, type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, publicErrorMessage, type InputKind, type PublicConfig } from "../api/client";
import { MonthlyFundingPanel } from "../components/MonthlyFundingPanel";
import { isMergedTranscriptionFile } from "../features/upload/MergedTranscriptionInput";
import { acceptsAudioFile, AUDIO_ACCEPT, AUDIO_FORMATS, suppliedAudioFormat } from "../features/upload/audioFormats";
import { clearPending, getPending, setPending, type PendingUpload } from "../features/upload/pending";
import { withSecret } from "../routing/secret";
import { clearDraft, loadDraft, saveDraft } from "../storage/draft";
import { UploadSessionPage } from "./UploadSessionPage";

type SelectedAudio = { key: string; file: File; person: string };

const isZipFile = (file: File): boolean => /\.zip$/i.test(file.name);

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
  const [inputKind, setInputKind] = useState<InputKind | null>(null);
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

  const selectAudio = (supplied: File[]) => {
    const additions = supplied.map((file) => ({ key: crypto.randomUUID(), file, person: personFor(file) }));
    if (!additions.length) return;
    setError(null);
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

  const expectedFormats = [
    config.input_modes.includes("audio") ? AUDIO_FORMATS : null,
    config.input_modes.includes("zip") ? "ZIP" : null,
    config.input_modes.includes("merged_transcription") ? "YAML, YML" : null,
  ].filter(Boolean).join(", ");

  const selectInputs = (supplied: File[]) => {
    if (!supplied.length) return;
    const allAudio = supplied.every(acceptsAudioFile);
    const oneZip = supplied.length === 1 && isZipFile(supplied[0]);
    const oneTranscription = supplied.length === 1 && isMergedTranscriptionFile(supplied[0]);
    if (audioSession && !allAudio) {
      setError(t("new.input_locked_audio"));
      return;
    }
    if (allAudio && config.input_modes.includes("audio")) {
      setInputKind("audio");
      setZipFile(null);
      setMergedFile(null);
      selectAudio(supplied);
      return;
    }
    if (oneZip && config.input_modes.includes("zip")) {
      setInputKind("zip");
      setFiles([]);
      setMergedFile(null);
      setZipFile(supplied[0]);
      setError(null);
      return;
    }
    if (oneTranscription && config.input_modes.includes("merged_transcription")) {
      setInputKind("merged_transcription");
      setFiles([]);
      setZipFile(null);
      setMergedFile(supplied[0]);
      setError(null);
      return;
    }
    setError(t("new.input_auto_invalid", {
      expected: expectedFormats,
      provided: [...new Set(supplied.map(suppliedAudioFormat))].join(", "),
    }));
  };

  const selectFromPicker = (event: ChangeEvent<HTMLInputElement>) => {
    const supplied = Array.from(event.target.files ?? []);
    event.target.value = "";
    selectInputs(supplied);
  };

  const selectFromDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    if (!busy) selectInputs(Array.from(event.dataTransfer.files));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!inputKind) {
      setError(t("new.input_required"));
      return;
    }
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

  return <main className="page form-page"><header className="page-header"><p className="eyebrow">{t("new.eyebrow")}</p><h1>{t("new.title")}</h1><p className="lede">{t("new.lede")}</p></header><form className="analysis-form" onSubmit={submit}>
    <label className="dropzone" aria-describedby="automatic-input-hint" onDragOver={(event) => event.preventDefault()} onDrop={selectFromDrop}>
      <strong>{t("new.input_auto_title")}</strong>
      <span id="automatic-input-hint">{t("new.input_auto_hint", { formats: expectedFormats })}</span>
      <input aria-label={t("new.input_auto_title")} type="file" accept={[config.input_modes.includes("audio") ? AUDIO_ACCEPT : null, config.input_modes.includes("zip") ? ".zip,application/zip" : null, config.input_modes.includes("merged_transcription") ? ".yaml,.yml,application/yaml,text/yaml" : null].filter(Boolean).join(",")} multiple disabled={busy} onChange={selectFromPicker} />
      {inputKind && <span className="selected-file">{t("new.input_detected", { type: t(`new.${inputKind}`) })}</span>}
    </label>
    {!audioSession && inputKind === "audio" && files.length > 0 && <ul className="file-list">{files.map((item) => <li key={item.key}><strong>{item.file.name}</strong></li>)}</ul>}
    {!audioSession && inputKind === "zip" && zipFile && <ul className="file-list"><li><strong>{zipFile.name}</strong></li></ul>}
    {!audioSession && inputKind === "merged_transcription" && mergedFile && <ul className="file-list"><li><strong>{mergedFile.name}</strong></li></ul>}
    {audioSession && <UploadSessionPage sessionId={audioSession.sessionId} secret={audioSession.secret} config={config} go={go} embedded autoLaunch={false} pendingRevision={pendingRevision} onSnapshot={setAudioSnapshot} onPendingChange={setAudioPending} />}
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
  </form><MonthlyFundingPanel /></main>;
}
