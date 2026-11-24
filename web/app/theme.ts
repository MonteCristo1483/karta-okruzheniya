import type { SkySpecification } from "maplibre-gl";

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

export const MAP_FOG: Record<Theme, SkySpecification> = {
  light: {
    "sky-color": "#f7f2e8",
    "horizon-color": "#eee7db",
    "fog-color": "#eee7db",
    "sky-horizon-blend": 0.45,
    "horizon-fog-blend": 0.45,
    "fog-ground-blend": 0.72,
    "atmosphere-blend": 0.25,
  },
  dark: {
    "sky-color": "#111822",
    "horizon-color": "#1c2635",
    "fog-color": "#1c2635",
    "sky-horizon-blend": 0.45,
    "horizon-fog-blend": 0.45,
    "fog-ground-blend": 0.72,
    "atmosphere-blend": 0.25,
  },
};

