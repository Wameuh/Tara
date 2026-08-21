import { useTranslation } from "react-i18next";

export function HelpPage({ onNew }: { onNew: () => void }) {
  const { t } = useTranslation();
  return <main className="page help-page">
    <p className="eyebrow">{t("help.eyebrow")}</p>
    <h1>{t("help.title")}</h1>
    <p className="lede">{t("help.lede")}</p>
    <div className="help-grid">
      <section><h2>{t("help.inputs_title")}</h2><p>{t("help.inputs_body")}</p></section>
      <section><h2>{t("help.link_title")}</h2><p>{t("help.link_body")}</p></section>
      <section><h2>{t("help.resume_title")}</h2><p>{t("help.resume_body")}</p></section>
      <section><h2>{t("help.status_title")}</h2><p>{t("help.status_body")}</p></section>
      <section><h2>{t("help.retention_title")}</h2><p>{t("help.retention_body")}</p></section>
      <section><h2>{t("help.privacy_title")}</h2><p>{t("help.privacy_body")}</p></section>
    </div>
    <button className="primary" onClick={onNew}>{t("help.start")}</button>
  </main>;
}
