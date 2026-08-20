import blackLogo from "../../assets/brand/tara-logo-black.png";
import blackLogo2x from "../../assets/brand/tara-logo-black@2x.png";
import "./logo.css";

export type LogoVariant = "black";
export type LogoSize = "header" | "loading" | "standalone";

type Props = {
  variant: LogoVariant;
  size: LogoSize;
  decorative: boolean;
  className?: string;
};

export function logoSources(variant: LogoVariant) {
  return { src: blackLogo, srcSet: `${blackLogo2x} 2x`, variant };
}

export function TaraLogo({ variant, size, decorative, className = "" }: Props) {
  const source = logoSources(variant);
  return <img className={`tara-logo tara-logo--${size} ${className}`.trim()} data-variant={variant} src={source.src} srcSet={source.srcSet} width="132" height="44" alt={decorative ? "" : "Tara"} aria-hidden={decorative || undefined} />;
}
