import { useTranslation } from "react-i18next";

export function DualProgress({ overall, current }: { overall?: number | null; current?: number | null }) {
  const { t } = useTranslation();
  return <section className="total-progress">
    <div className="progress-overall">
      <div className="progress-heading"><label>{t("job.total")}</label><strong>{Math.round((overall ?? 0) * 100)} %</strong></div>
      <progress value={overall ?? 0} max="1" aria-label={t("job.total")} />
    </div>
    {current !== null && current !== undefined && <div className="progress-current">
      <label>{t("job.current")}</label>
      <progress value={current} max="1" aria-label={t("job.current")} />
    </div>}
  </section>;
}
