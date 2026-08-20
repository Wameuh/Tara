import { useEffect } from "react";
import { readSection } from "../../../routing/secret";
export function useSectionNavigation(open: Set<string>, setOpen: (value: Set<string>) => void) { useEffect(() => { const id = readSection(); if (!id) return; setOpen(new Set([...open, id])); requestAnimationFrame(() => document.getElementById(id)?.focus()); }, []); }
