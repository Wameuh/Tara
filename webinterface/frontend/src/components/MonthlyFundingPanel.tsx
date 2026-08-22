import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, type MonthlyFundingSnapshot } from "../api/client";

const REFRESH_MS = 60_000;

export function MonthlyFundingPanel() {
  const { t, i18n } = useTranslation();
  const [snapshot, setSnapshot] = useState<MonthlyFundingSnapshot | null>(null);

  useEffect(() => {
    let active = true;
    const refresh = () => void api.getMonthlyFunding()
      .then((value) => { if (active) setSnapshot(value); })
      .catch(() => undefined);
    refresh();
    const timer = window.setInterval(refresh, REFRESH_MS);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const money = useMemo(() => new Intl.NumberFormat(i18n.language, {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }), [i18n.language]);
  const month = useMemo(() => new Intl.DateTimeFormat(i18n.language, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }), [i18n.language]);

  if (!snapshot?.enabled) return null;
  const donations = snapshot.donations_micro_eur / 1_000_000;
  const consumption = snapshot.estimated_consumption_micro_eur / 1_000_000;
  const goal = snapshot.monthly_goal_micro_eur === null ? null : snapshot.monthly_goal_micro_eur / 1_000_000;
  const scale = Math.max(goal ?? 0, donations, consumption, 1);
  const period = month.format(new Date(`${snapshot.month}-01T12:00:00Z`));

  return <section className="funding-panel" aria-labelledby="funding-title">
    <header className="funding-header">
      <div><p className="eyebrow">{t("funding.period", { month: period })}</p><h2 id="funding-title">{t("funding.title")}</h2></div>
      {snapshot.kofi_page_url && <a className="kofi-link" href={snapshot.kofi_page_url} target="_blank" rel="noreferrer">{t("funding.support")}</a>}
    </header>
    <p className="funding-intro">{t("funding.intro")}</p>
    <div className="funding-bars">
      <div className="funding-row">
        <div className="funding-label"><strong>{t("funding.donations")}</strong><span>{money.format(donations)}</span></div>
        <progress className="donation-progress" value={donations} max={scale} aria-label={t("funding.donations_progress", { value: money.format(donations) })} />
      </div>
      <div className="funding-row">
        <div className="funding-label"><strong>{t("funding.consumption")}</strong><span>{money.format(consumption)}{snapshot.estimate_partial ? ` · ${t("funding.partial")}` : ""}</span></div>
        <progress className="consumption-progress" value={consumption} max={scale} aria-label={t("funding.consumption_progress", { value: money.format(consumption) })} />
      </div>
    </div>
    <footer className="funding-footer">
      <span>{goal === null ? t("funding.shared_scale") : t("funding.goal", { value: money.format(goal) })}</span>
    </footer>
  </section>;
}
