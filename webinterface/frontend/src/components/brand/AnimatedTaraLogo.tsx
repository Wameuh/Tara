import { useEffect, useState } from "react";
import { logoSources, TaraLogo, type LogoSize, type LogoVariant } from "./TaraLogo";

let playedInTab = false;

type Props = {
  variant: LogoVariant;
  size: LogoSize;
  play: boolean;
  onAnimationEnd?: () => void;
};

function motionAllowed(): boolean {
  const connection = navigator as Navigator & { connection?: { saveData?: boolean } };
  return !(typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches)
    && !connection.connection?.saveData
    && document.visibilityState !== "hidden";
}

export function AnimatedTaraLogo({ variant, size, play, onAnimationEnd }: Props) {
  const [animated, setAnimated] = useState(false);
  const source = logoSources(variant);
  useEffect(() => {
    if (!play || playedInTab || !motionAllowed()) return;
    const timer = window.setTimeout(() => {
      playedInTab = true;
      setAnimated(true);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [play]);

  return <span className={`tara-logo-animation tara-logo--${size}`} data-variant={variant}>
    <TaraLogo variant={variant} size={size} decorative={false} className="tara-logo-animation__base" />
    {animated && <>
      <span className="tara-logo-animation__mask" aria-hidden="true" />
      <img className="tara-logo tara-logo-animation__d20" src={source.src} srcSet={source.srcSet} width="132" height="44" alt="" aria-hidden="true" onError={() => setAnimated(false)} onAnimationEnd={() => { setAnimated(false); onAnimationEnd?.(); }} />
    </>}
  </span>;
}
