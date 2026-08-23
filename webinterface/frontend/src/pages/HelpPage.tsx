import { AudioLines, BookOpenText, Bot, FileArchive, Hourglass, ScrollText } from "lucide-react";
import { useTranslation } from "react-i18next";

export function HelpPage({ onNew }: { onNew: () => void }) {
  const { t } = useTranslation();
  return <main className="page help-page">
    <header className="page-header"><p className="eyebrow">{t("help.eyebrow")}</p><h1>{t("help.title")}</h1><p className="lede">{t("help.lede")}</p></header>
    <section className="quick-start" aria-labelledby="quick-start-title">
      <div className="quick-start__intro">
        <p className="eyebrow">{t("help.quick_eyebrow")}</p>
        <h2 id="quick-start-title">{t("help.quick_title")}</h2>
        <p>{t("help.quick_body")}</p>
      </div>
      <ol className="quick-start__steps">
        <li><AudioLines aria-hidden="true" /><div><strong>{t("help.step_audio_title")}</strong><span>{t("help.step_audio_body")}</span></div></li>
        <li><BookOpenText aria-hidden="true" /><div><strong>{t("help.step_context_title")}</strong><span>{t("help.step_context_body")}</span></div></li>
        <li><Hourglass aria-hidden="true" /><div><strong>{t("help.step_wait_title")}</strong><span>{t("help.step_wait_body")}</span></div></li>
        <li><ScrollText aria-hidden="true" /><div><strong>{t("help.step_result_title")}</strong><span>{t("help.step_result_body")}</span></div></li>
      </ol>
    </section>
    <section className="craig-guide" aria-labelledby="craig-title">
      <div className="craig-guide__heading">
        <Bot aria-hidden="true" size={30} />
        <div><p className="eyebrow">{t("help.craig_eyebrow")}</p><h2 id="craig-title">{t("help.craig_title")}</h2></div>
      </div>
      <p className="craig-guide__lede">{t("help.craig_body")}</p>
      <ol className="craig-guide__steps">
        <li><span>01</span><div><strong>{t("help.craig_record_title")}</strong><p>{t("help.craig_record_body")}</p></div></li>
        <li><span>02</span><div><strong>{t("help.craig_download_title")}</strong><p>{t("help.craig_download_body")}</p></div></li>
        <li><span>03</span><div><strong>{t("help.craig_import_title")}</strong><p>{t("help.craig_import_body")}</p></div></li>
      </ol>
      <div className="craig-guide__footer">
        <p><FileArchive aria-hidden="true" size={18} />{t("help.craig_tip")}</p>
        <a href="https://docs.craig.chat/" target="_blank" rel="noreferrer">{t("help.craig_link")}</a>
      </div>
    </section>
    <section className="context-guide" aria-labelledby="context-guide-title">
      <div><p className="eyebrow">{t("help.context_eyebrow")}</p><h2 id="context-guide-title">{t("help.context_title")}</h2><p>{t("help.context_body")}</p></div>
      <ul>
        <li>{t("help.context_game")}</li>
        <li>{t("help.context_people")}</li>
        <li>{t("help.context_campaign")}</li>
        <li>{t("help.context_previous")}</li>
      </ul>
    </section>
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
