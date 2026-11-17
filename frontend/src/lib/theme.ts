import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark" | "system";
const KEY = "sentinel.theme";
const listeners = new Set<() => void>();

function read(): Theme {
  try { return (localStorage.getItem(KEY) as Theme) || "system"; } catch { return "system"; }
}

function apply(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function setTheme(theme: Theme) {
  try { localStorage.setItem(KEY, theme); } catch { /* storage unavailable */ }
  apply(theme);
  listeners.forEach((l) => l());
}

export function initTheme() {
  apply(read());
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (read() === "system") { apply("system"); listeners.forEach((l) => l()); }
  });
}

/** Shared theme state: every component sees the same value. `dark` is the resolved appearance. */
export function useTheme() {
  const theme = useSyncExternalStore((cb) => { listeners.add(cb); return () => listeners.delete(cb); }, read);
  const dark = useSyncExternalStore((cb) => { listeners.add(cb); return () => listeners.delete(cb); },
    () => document.documentElement.classList.contains("dark"));
  return { theme, dark, setTheme };
}
