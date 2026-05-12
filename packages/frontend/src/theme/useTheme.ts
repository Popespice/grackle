import { create } from "zustand";

export type Theme = "dark" | "light";

interface ThemeState {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggle: () => void;
}

function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  const stored = localStorage.getItem("grackle:theme");
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("grackle:theme", theme);
}

export const useTheme = create<ThemeState>()(() => {
  const initial = getInitialTheme();
  applyTheme(initial);
  return {
    theme: initial,
    setTheme: (theme: Theme) => {
      applyTheme(theme);
      useTheme.setState({ theme });
    },
    toggle: () => {
      const next = useTheme.getState().theme === "dark" ? "light" : "dark";
      applyTheme(next);
      useTheme.setState({ theme: next });
    },
  };
});
