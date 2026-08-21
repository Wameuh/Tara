import { CircleHelp, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BrandLink } from "./brand/BrandLink";

export function AppHeader({ onHome, onHelp }: { onHome: () => void; onHelp: () => void }) {
  const { t } = useTranslation();
  return <header className="app-header"><BrandLink onHome={onHome} /><button className="icon-text" onClick={onHome}><Plus aria-hidden="true" size={18} />{t("nav.new")}</button><button className="help" onClick={onHelp}><CircleHelp aria-hidden="true" size={18} />{t("nav.help")}</button></header>;
}
