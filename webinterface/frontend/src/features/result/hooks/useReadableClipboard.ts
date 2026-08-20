import { useCallback, useState } from "react";
export function useReadableClipboard() { const [error, setError] = useState(false); const copy = useCallback(async (title: string, text: string) => { try { await navigator.clipboard.writeText(`${title}\n\n${text}`); setError(false); return true; } catch { setError(true); return false; } }, []); return { copy, error }; }
