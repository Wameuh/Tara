import { useTranslation } from "react-i18next";
import type { JobSnapshot } from "../../../api/client";

export function StageTimeline({ job }: { job: JobSnapshot }) {
  const { t } = useTranslation();
  return <ol className="timeline">{job.stages.map((stage, index) => <li key={stage.code} className={stage.status} aria-current={stage.status === "active" ? "step" : undefined}>
    <span className="stage-index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
    <div className="stage-copy">
      <strong>{t(`stage.${stage.code}` as never, { defaultValue: stage.code.replaceAll("_", " ") })}</strong>
      {stage.status === "active" && <>
        {job.progress?.substage_code && <span>{t(`substage.${job.progress.substage_code}` as never, { defaultValue: job.progress.substage_code.replaceAll("_", " ") })}</span>}
        {stage.progress !== null && <progress value={stage.progress ?? 0} max="1" aria-label={t("job.current")} />}
      </>}
    </div>
  </li>)}</ol>;
}
