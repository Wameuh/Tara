import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchPublicConfig, type PublicConfig } from "./api/client";
import { AppHeader } from "./components/AppHeader";
import { ToastRegion } from "./components/ToastRegion";
import { AnimatedTaraLogo } from "./components/brand/AnimatedTaraLogo";
import { HelpPage } from "./pages/HelpPage";
import { JobPage } from "./pages/JobPage";
import { NewJobPage } from "./pages/NewJobPage";
import { UploadSessionPage } from "./pages/UploadSessionPage";
import { readSecret } from "./routing/secret";
function usePath() { const [path, setPath] = useState(location.pathname); useEffect(() => { const listener = () => setPath(location.pathname); addEventListener("popstate", listener); return () => removeEventListener("popstate", listener); }, []); return [path, (next: string) => { history.pushState(null, "", next); setPath(location.pathname); }] as const; }
export default function App() { const { t, i18n } = useTranslation(); const [config, setConfig] = useState<PublicConfig | null>(null); const [warning, setWarning] = useState(false); const [path, go] = usePath(); useEffect(() => { let active = true; void fetchPublicConfig().then(async value => { await i18n.changeLanguage(value.language); if (!active) return; document.documentElement.lang = value.locale; setConfig(value); }).catch(async () => { await i18n.changeLanguage("fr"); if (!active) return; document.documentElement.lang = "fr-FR"; setWarning(true); setConfig({ language: "fr", locale: "fr-FR", supported_languages: ["fr"], input_modes: ["audio", "merged_transcription", "zip"], max_upload_bytes: 1, recommended_chunk_bytes: 16384, max_chunk_bytes: 16384, parallel_uploads: 1 }); }); return () => { active = false; }; }, [i18n]); if (!config) return <main className="loading"><AnimatedTaraLogo variant="black" size="loading" play /><p>{t("app.loading")}</p></main>; const session = /^\/sessions\/([A-Za-z0-9_-]+)$/.exec(path)?.[1]; const job = /^\/jobs\/([A-Za-z0-9_-]+)$/.exec(path)?.[1]; const secret = readSecret(); return <><AppHeader onHome={() => go("/")} onHelp={() => go("/help")} /><ToastRegion message={warning ? t("warnings.config_unavailable") : null} />{path === "/help" ? <HelpPage onNew={() => go("/")} /> : session ? <UploadSessionPage sessionId={session} secret={secret} config={config} go={go} /> : job ? <JobPage jobId={job} secret={secret} locale={config.locale} go={go} /> : <NewJobPage config={config} go={go} />}</>; }
