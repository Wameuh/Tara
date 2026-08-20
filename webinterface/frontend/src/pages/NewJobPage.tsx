import { type ChangeEvent, type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, type InputKind, type PublicConfig } from "../api/client";
import { MergedTranscriptionInput } from "../features/upload/MergedTranscriptionInput";
import { ZipInput } from "../features/upload/ZipInput";
import { setPending, type PendingUpload } from "../features/upload/pending";
import { withSecret } from "../routing/secret";
import { clearDraft, loadDraft, saveDraft } from "../storage/draft";

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
  const draft = loadDraft();
  const [inputKind, setInputKind] = useState<InputKind>("audio");
  const [files, setFiles] = useState<SelectedAudio[]>([]);
  const [mergedFile, setMergedFile] = useState<File | null>(null);
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [language, setLanguage] = useState(draft?.language ?? config.language);
  const [context, setContext] = useState(draft?.contextText ?? "");
  const [summaries, setSummaries] = useState(draft?.summariesText ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const selected = inputKind === "audio" ? files.map(({ file }) => file) : inputKind === "zip" ? (zipFile ? [zipFile] : []) : mergedFile ? [mergedFile] : [];
    if (!selected.length) {
      setError(t(inputKind === "audio" ? "new.file_required" : inputKind === "zip" ? "new.zip_invalid" : "new.merged_transcription_invalid"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.createUploadSession(inputKind);
      await api.updateSessionInputs(created.session_id, created.secret, created.revision, { language, context_text: context, previous_summaries_text: summaries });
      setPending(created.session_id, selected.map((file, index) => ({
        key: inputKind === "audio" ? files[index].key : crypto.randomUUID(),
        file,
        inputKind,
        ...(inputKind === "audio" ? { person: files[index].person } : {}),
        state: "queued",
        hashingLoaded: 0,
        confirmedOffset: 0,
        idempotencyKey: crypto.randomUUID(),
      }) satisfies PendingUpload));
      clearDraft();
      go(withSecret(`/sessions/${created.session_id}`, created.secret));
    } catch {
      setError(t("errors.generic"));
      setBusy(false);
    }
  };

  return <main className="page form-page"><p className="eyebrow">{t("new.eyebrow")}</p><h1>{t("new.title")}</h1><p className="lede">{t("new.lede")}</p><form onSubmit={submit}>
    <fieldset className="input-kind" aria-describedby="input-kind-hint"><legend>{t("new.input_kind")}</legend><p id="input-kind-hint" className="muted">{t("new.input_kind_hint")}</p><div className="input-kind-options">
      <label><input type="radio" name="input-kind" value="audio" checked={inputKind === "audio"} onChange={() => { setInputKind("audio"); setError(null); }} />{t("new.audio")}</label>
      <label><input type="radio" name="input-kind" value="merged_transcription" checked={inputKind === "merged_transcription"} onChange={() => { setInputKind("merged_transcription"); setError(null); }} />{t("new.merged_transcription")}</label>
      <label><input type="radio" name="input-kind" value="zip" checked={inputKind === "zip"} onChange={() => { setInputKind("zip"); setError(null); }} />{t("new.zip")}</label>
    </div></fieldset>
    {inputKind === "audio" ? <><label className="dropzone"><strong>{t("new.audio")}</strong><span>{t("new.audio_hint")}</span><input type="file" accept="audio/mpeg,audio/ogg,.mp3,.ogg" multiple onChange={(event) => setFiles(Array.from(event.target.files ?? []).map((file) => ({ key: crypto.randomUUID(), file, person: personFor(file) })))} /></label>
      {files.length > 0 && <ul className="file-list">{files.map((item) => <li key={item.key}><strong>{item.file.name}</strong><label>{t("upload.person")}<input value={item.person} onChange={(event) => setFiles((current) => current.map((value) => value.key === item.key ? { ...value, person: event.target.value } : value))} /></label></li>)}</ul>}</> : inputKind === "zip" ? <ZipInput file={zipFile} onChange={(file) => { setZipFile(file); setError(file ? null : t("new.zip_invalid")); }} /> : <MergedTranscriptionInput file={mergedFile} onChange={(file) => { setMergedFile(file); setError(file ? null : t("new.merged_transcription_invalid")); }} />}
    <div className="field-grid"><label>{t("new.context")}<textarea value={context} onChange={(event) => setContext(event.target.value)} rows={6} /></label><label>{t("new.summaries")}<textarea value={summaries} onChange={(event) => setSummaries(event.target.value)} rows={6} /></label></div>
    <div className="form-actions">{config.supported_languages.length > 1 && <label>{t("job.language")}<select value={language} onChange={(event) => setLanguage(event.target.value)}>{config.supported_languages.map((item) => <option key={item}>{item}</option>)}</select></label>}
      <label className="file-text">{t("new.load_context")}<input type="file" multiple accept=".txt,.md,text/plain,text/markdown" onChange={(event) => void importText(event, context, 200_000, setContext)} /></label>
      <label className="file-text">{t("new.load_summaries")}<input type="file" multiple accept=".txt,.md,text/plain,text/markdown" onChange={(event) => void importText(event, summaries, 2_000_000, setSummaries)} /></label>
      <button className="primary" disabled={busy}>{t(busy ? "new.preparing" : "new.submit")}</button></div>
    {error && <p className="error" role="alert">{error}</p>}
  </form></main>;
}
