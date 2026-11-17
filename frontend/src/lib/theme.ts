import { useEffect, useState } from "react";

export type Theme = "light" | "dark" | "system";
const KEY = "sentinel.theme";

function apply(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function initTheme() {
  let stored: Theme = "system";
  try { stored = (localStorage.getItem(KEY) as Theme) || "system"; } catch { /* storage unavailable */ }
  apply(stored);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    let current: Theme = "system";
    try { current = (localStorage.getItem(KEY) as Theme) || "system"; } catch { /* ignore */ }
    if (current === "system") apply("system");
  });
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    try { return (localStorage.getItem(KEY) as Theme) || "system"; } catch { return "system"; }
  });
  useEffect(() => {
    apply(theme);
    try { localStorage.setItem(KEY, theme); } catch { /* ignore */ }
  }, [theme]);
  return { theme, setTheme };
}
