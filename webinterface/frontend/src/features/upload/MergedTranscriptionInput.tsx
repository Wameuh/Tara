import { type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";

export const isMergedTranscriptionFile = (file: File): boolean => /\.ya?ml$/i.test(file.name);

export function MergedTranscriptionInput({ file, onChange }: {
  file: File | null;
  onChange: (file: File | null) => void;
}) {
  const { t } = useTranslation();
  const selectFile = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = event.target.files?.[0] ?? null;
    event.target.value = "";
    onChange(selected && isMergedTranscriptionFile(selected) ? selected : null);
  };

  return <label className="dropzone" aria-describedby="merged-transcription-hint">
    <strong>{t("new.merged_transcription")}</strong>
    <span id="merged-transcription-hint">{t("new.merged_transcription_hint")}</span>
    <input aria-label={t("new.merged_transcription")} type="file" accept=".yaml,.yml,application/yaml,text/yaml" onChange={selectFile} />
    {file && <span className="selected-file">{file.name}</span>}
  </label>;
}
