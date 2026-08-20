import { TaraLogo } from "./TaraLogo";

export function BrandLink({ onHome }: { onHome?: () => void }) {
  return <a className="brand-link" href="/" aria-label="Tara - accueil" onClick={onHome ? (event) => { event.preventDefault(); onHome(); } : undefined}><TaraLogo variant="black" size="header" decorative /></a>;
}
