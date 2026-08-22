import { useTranslation } from "react-i18next";

export function HelpPage({ onNew }: { onNew: () => void }) {
  const { t } = useTranslation();
  return <main className="page help-page">
    <header className="page-header"><p className="eyebrow">{t("help.eyebrow")}</p><h1>{t("help.title")}</h1><p className="lede">{t("help.lede")}</p></header>
    <div className="help-grid">
      <section><h2>{t("help.inputs_title")}</h2><p>{t("help.inputs_body")}</p></section>
      <section><h2>{t("help.link_title")}</h2><p>{t("help.link_body")}</p></section>
      <section><h2>{t("help.resume_title")}</h2><p>{t("help.resume_body")}</p></section>
      <section><h2>{t("help.status_title")}</h2><p>{t("help.status_body")}</p></section>
      <section><h2>{t("help.privacy_title")}</h2><p>{t("help.privacy_body")}</p></section>
      <section className="retention-section">
        <h2>{t("help.retention_title")}</h2>
        <p>{t("help.retention_body")}</p>
        <dl className="retention-policy">
          <div><dt>{t("help.retention_session_label")}</dt><dd>{t("help.retention_session_body")}</dd></div>
          <div><dt>{t("help.retention_work_label")}</dt><dd>{t("help.retention_work_body")}</dd></div>
          <div><dt>{t("help.retention_result_label")}</dt><dd>{t("help.retention_result_body")}</dd></div>
          <div><dt>{t("help.retention_delete_label")}</dt><dd>{t("help.retention_delete_body")}</dd></div>
          <div className="retention-policy__warning"><dt>{t("help.retention_database_label")}</dt><dd>{t("help.retention_database_body")}</dd></div>
        </dl>
        <p className="retention-note">{t("help.retention_cleanup_note")}</p>
      </section>
    </div>
    <button className="primary" onClick={onNew}>{t("help.start")}</button>
  </main>;
}
