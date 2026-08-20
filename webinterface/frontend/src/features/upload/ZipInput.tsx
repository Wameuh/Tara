import { type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";

export function ZipInput({ file, onChange }: {
  file: File | null;
  onChange: (file: File | null) => void;
}) {
  const { t } = useTranslation();
  const selectFile = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = event.target.files?.[0] ?? null;
    event.target.value = "";
    onChange(selected && /\.zip$/i.test(selected.name) ? selected : null);
  };
  return <label className="dropzone" aria-describedby="zip-hint">
    <strong>{t("new.zip")}</strong>
    <span id="zip-hint">{t("new.zip_hint")}</span>
    <input aria-label={t("new.zip")} type="file" accept=".zip,application/zip" onChange={selectFile} />
    {file && <span className="selected-file">{file.name}</span>}
  </label>;
}
