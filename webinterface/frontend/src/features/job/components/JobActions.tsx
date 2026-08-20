import { useTranslation } from "react-i18next";
import type { JobSnapshot } from "../../../api/client";

type Props = { job: JobSnapshot; onCancel: () => void; onRelaunch: () => void; onEdit: () => void; onCopy: () => void; onRotate?: () => void; disabled?: boolean };

export function JobActions({ job, onCancel, onRelaunch, onEdit, onCopy, onRotate = () => {}, disabled = false }: Props) {
  const { t } = useTranslation();
  return <div className="job-actions"><button disabled={disabled} onClick={onCopy}>{t("job.copy_link")}</button>{job.allowed_actions.includes("regenerate_secret") && <button disabled={disabled} onClick={onRotate}>{t("job.rotate_secret")}</button>}{job.allowed_actions.includes("cancel") && <button disabled={disabled} className="danger" onClick={onCancel}>{t("job.cancel")}</button>}{job.allowed_actions.includes("relaunch_identical") && <button disabled={disabled} onClick={onRelaunch}>{t("job.relaunch")}</button>}{job.allowed_actions.includes("edit_and_relaunch") && <button disabled={disabled} onClick={onEdit}>{t("job.edit")}</button>}<p className="muted">{t("job.share_warning")}</p></div>;
}
