import { CircleHelp, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BrandLink } from "./brand/BrandLink";

export function AppHeader({ onHome }: { onHome: () => void }) {
  const { t } = useTranslation();
  return <header className="app-header"><BrandLink onHome={onHome} /><button className="icon-text" onClick={onHome}><Plus aria-hidden="true" size={18} />{t("nav.new")}</button><a className="help" href="mailto:help@example.invalid"><CircleHelp aria-hidden="true" size={18} />{t("nav.help")}</a></header>;
}
