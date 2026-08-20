import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ResultSnapshot } from "../../../api/client";
export function ResultHeader({ result, locale }: { result: ResultSnapshot; locale: string }) {
  const { t } = useTranslation();
  const [explaining, setExplaining] = useState(false);
  const value = result.cost.value_micro_eur;
  const cost = value == null ? t("result.cost_unavailable") : new Intl.NumberFormat(locale, { style: "currency", currency: "EUR", minimumFractionDigits: 5, maximumFractionDigits: 5 }).format(value / 1_000_000);
  return <header className="result-banner"><div><h1>{t("result.title")}</h1><p>{t("result.expiration", { date: result.expires_at ? new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(result.expires_at)) : "-" })}</p></div><div><small>{result.cost.status === "partial" ? t("result.cost_partial") : t("result.cost")}</small><button type="button" className="info-button" aria-label={t("result.cost_information")} aria-expanded={explaining} onClick={() => setExplaining((value) => !value)}>i</button><strong>{cost}</strong>{explaining && <p role="tooltip">{t(result.cost.explanation_key as never)}</p>}</div></header>;
}
