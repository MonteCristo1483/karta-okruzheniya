export type Theme = "light" | "dark";

export const THEME_KEY = "karta-theme";

export function initialTheme(): Theme {
  if (typeof window === "undefined") return "light";
  try {
    return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}
