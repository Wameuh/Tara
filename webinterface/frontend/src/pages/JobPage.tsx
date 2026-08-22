/* eslint-disable @typescript-eslint/no-unused-expressions */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, publicErrorMessage, type ResultSnapshot } from "../api/client";
import { ToastRegion } from "../components/ToastRegion";
import { JobActions } from "../features/job/components/JobActions";
import { DualProgress } from "../features/job/components/DualProgress";
import { JobInfoPanel } from "../features/job/components/JobInfoPanel";
import { StageTimeline } from "../features/job/components/StageTimeline";
import { useJobSnapshot } from "../features/job/hooks/useJobSnapshot";
import { ResultHeader } from "../features/result/components/ResultHeader";
import { ResultNavigation } from "../features/result/components/ResultNavigation";
import { PublicBlockRenderer } from "../features/result/components/PublicBlockRenderer";
import { ResultSearch } from "../features/result/components/ResultSearch";
import { ResultSection } from "../features/result/components/ResultSection";
import { useReadableClipboard } from "../features/result/hooks/useReadableClipboard";
import { useResultSearch } from "../features/result/hooks/useResultSearch";
import { useSectionNavigation } from "../features/result/hooks/useSectionNavigation";
import { downloadSessionSummary } from "../features/result/sessionSummaryMarkdown";
import { withSecret } from "../routing/secret";

export function JobPage({ jobId, secret, locale, go }: { jobId: string; secret?: string; locale: string; go: (path: string) => void }) {
  const { t } = useTranslation();
  const apiError = (reason: unknown) => publicErrorMessage(reason, t("errors.generic"), (code) => t("errors.support_code", { code }));
  const [currentSecret, setCurrentSecret] = useState(secret);
  const { job, error, disconnected, refresh } = useJobSnapshot(jobId, currentSecret);
  const [result, setResult] = useState<ResultSnapshot | null>(null);
  const [resultError, setResultError] = useState(false);
  const [loadingResult, setLoadingResult] = useState(false);
  const [announced, setAnnounced] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [command, setCommand] = useState(false);

  useEffect(() => {
    setCurrentSecret(secret);
    setResult(null);
    setResultError(false);
  }, [jobId, secret]);

  const loadResult = useCallback(async () => {
    if (!currentSecret || loadingResult) return;
    setLoadingResult(true);
    setResultError(false);
    try {
      setResult(await api.getResult(jobId, currentSecret));
      if (!announced) {
        setAnnounced(true);
        setToast(t("toast.result_ready"));
      }
    } catch {
      setResultError(true);
    } finally {
      setLoadingResult(false);
    }
  }, [announced, currentSecret, jobId, loadingResult, t]);

  useEffect(() => {
    if (job?.status === "completed" && !result) void loadResult();
  }, [job?.status, loadResult, result]);

  const run = async (action: () => Promise<unknown>) => {
    setCommand(true);
    try {
      await action();
      await refresh();
    } catch (reason) {
      setToast(apiError(reason));
    } finally {
      setCommand(false);
    }
  };

  const rotateSecret = async () => {
    if (!currentSecret || !job) return;
    setCommand(true);
    try {
      const rotated = await api.regenerateSecret(jobId, currentSecret, job.revision);
      const destination = withSecret(`/jobs/${jobId}`, rotated.secret);
      history.replaceState(null, "", destination);
      setCurrentSecret(rotated.secret);
      setResult(null);
      setResultError(false);
      setToast(t("job.secret_rotated"));
    } catch (reason) {
      setToast(apiError(reason));
    } finally {
      setCommand(false);
    }
  };

  const relaunchIdentical = async () => {
    if (!currentSecret || !job) return;
    setCommand(true);
    try {
      const relaunched = await api.relaunchIdentical(jobId, currentSecret, job.revision);
      if (!relaunched.job_id) throw new Error("missing_relaunch_job");
      go(withSecret(`/jobs/${relaunched.job_id}`, currentSecret));
    } catch (reason) {
      setToast(apiError(reason));
    } finally {
      setCommand(false);
    }
  };

  if (!currentSecret || error) return <main className="page"><h1>{t("job.unavailable")}</h1></main>;
  if (result) return <ResultPage result={result} locale={locale} jobId={jobId} secret={currentSecret} toast={toast} />;
  if (!job) return <main className="page"><h1>{t("app.loading")}</h1></main>;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(new URL(withSecret(`/jobs/${jobId}`, currentSecret), location.origin).toString());
      setToast(t("job.link_copied"));
    } catch {
      setToast(t("errors.generic"));
    }
  };
  const errorMessage = job.error
    ? t(job.error.message_key as never, { defaultValue: t(`errors.${job.error.code}` as never, { defaultValue: t("errors.generic") }), ...job.error.parameters })
    : null;

  return <main className="page">
    <ToastRegion message={toast ?? (disconnected ? t("warnings.reconnecting") : null)} />
    <h1>{t(`job.${job.status}` as never, { defaultValue: job.status })}</h1>
    {errorMessage && <p className="error">{errorMessage}</p>}
    <div className="badges"><span>{t("job.attempt", { count: job.attempt_number })}</span><span>{t(`job.${job.status}` as never, { defaultValue: job.status })}</span></div>
    <DualProgress overall={job.progress?.overall_ratio} current={job.progress?.current_ratio} />
    <div className="job-grid"><StageTimeline job={job} /><div><JobInfoPanel job={job} locale={locale} /><div aria-busy={command}>
      <JobActions disabled={command} job={job} onCopy={() => void copy()} onRotate={() => void rotateSecret()}
        onCancel={() => void run(() => api.cancelJob(jobId, currentSecret, job.revision).then(() => setToast(t("toast.cancel_requested"))))}
        onRelaunch={() => void relaunchIdentical()}
        onEdit={() => void run(async () => { const session = await api.editAndRelaunch(jobId, currentSecret, job.revision); go(withSecret(`/sessions/${session.session_id}`, currentSecret)); })} />
    </div></div></div>
    {resultError && <button onClick={() => void loadResult()} disabled={loadingResult}>{t("upload.retry")}</button>}
  </main>;
}

function ResultPage({ result, locale, jobId, secret, toast }: { result: ResultSnapshot; locale: string; jobId: string; secret: string; toast: string | null }) {
  const { t } = useTranslation(); const sections = result.sections.slice().sort((a, b) => a.order - b.order); const overview = sections.find(section => section.section_type === "overview"); const rest = sections.filter(section => section !== overview); const [open, setOpen] = useState(() => new Set(rest.map(section => section.id))); const [matchIndex, setMatchIndex] = useState(0); const { input, setInput, query, matches } = useResultSearch(sections); const { copy, error } = useReadableClipboard(); const [copied, setCopied] = useState<string | null>(null); useSectionNavigation(open, setOpen); const focus = (id: string) => { if (id !== overview?.id) setOpen(new Set([...open, id])); requestAnimationFrame(() => document.getElementById(id)?.focus()); }; const move = (delta: number) => { if (!matches.length) return; const next = (matchIndex + delta + matches.length) % matches.length; setMatchIndex(next); focus(matches[next].id); }; const hrefFor = (id: string) => withSecret(`/jobs/${jobId}`, secret, id); if (result.status === "expired") return <main className="page"><ResultHeader result={result} locale={locale} /><p className="error">{t("result.expired")}</p></main>; return <main className="page result"><ToastRegion message={toast ?? copied ?? (error ? t("result.copy_failed") : null)} /><ResultHeader result={result} locale={locale} /><button className="summary-download" onClick={() => downloadSessionSummary(result, t("result.markdown_title"))}>{t("result.download_markdown")}</button>{result.summary_markdown ? <section className="summary-document" aria-labelledby="summary-document-title"><h2 id="summary-document-title">{t("result.markdown_title")}</h2><pre>{result.summary_markdown}</pre></section> : <>{overview && <section id={overview.id} tabIndex={-1}><h2>{overview.title || t("result.overview")}</h2><PublicBlockRenderer blocks={overview.blocks} query={query} /></section>}<div className="result-layout"><ResultNavigation sections={sections} hrefFor={hrefFor} label={t("result.sections")} onSelect={focus} /><div><label>{t("result.go_to")}<select value="" onChange={event => focus(event.target.value)}><option value="" disabled>{t("result.go_to")}</option>{sections.map(section => <option key={section.id} value={section.id}>{section.title}</option>)}</select></label><ResultSearch value={input} onChange={setInput} count={matches.length} previous={() => move(-1)} next={() => move(1)} /><button onClick={() => setOpen(new Set(rest.map(section => section.id)))}>{t("result.open_all")}</button><button onClick={() => setOpen(new Set())}>{t("result.close_all")}</button>{rest.map((section, index) => <ResultSection key={section.id} id={section.id} number={index + 1} title={section.title} open={open.has(section.id)} query={query} blocks={section.blocks} onToggle={() => setOpen(current => { const next = new Set(current); next.has(section.id) ? next.delete(section.id) : next.add(section.id); return next; })} onCopy={() => void copy(section.title, section.text).then(ok => ok && setCopied(t("result.copied")))} />)}</div></div></>}</main>;
}
