import type { Theme } from "./theme";

export type LayerKey =
  | "schools"
  | "kindergartens"
  | "parks"
  | "metro"
  | "shops"
  | "redzones"
  | "routes";

export type Visibility = Record<LayerKey, boolean>;

export const DEFAULT_VISIBILITY: Visibility = {
  schools: true,
  kindergartens: true,
  parks: true,
  metro: true,
  shops: false,
  redzones: true,
  routes: true,
};

const ITEMS: { key: LayerKey; label: string }[] = [
  { key: "schools", label: "Школы" },
  { key: "kindergartens", label: "Сады" },
  { key: "parks", label: "Парки" },
  { key: "metro", label: "Метро" },
  { key: "shops", label: "Магазины" },
  { key: "redzones", label: "Красные зоны" },
  { key: "routes", label: "Маршруты" },
];

function SunIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="4.5" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M5 5l1.7 1.7M17.3 17.3 19 19M19 5l-1.7 1.7M6.7 17.3 5 19" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M20 13.6A8.1 8.1 0 0 1 10.4 4a8.1 8.1 0 1 0 9.6 9.6Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}

export default function LayersPanel({
  visibility,
  onToggle,
  theme,
  onThemeToggle,
}: {
  visibility: Visibility;
  onToggle: (key: LayerKey) => void;
  theme: Theme;
  onThemeToggle: () => void;
}) {
  return (
    <div className="absolute left-3 top-3 z-10 rounded-2xl bg-white/90 p-3 shadow-lg backdrop-blur dark:bg-slate-900/90">
      <div className="mb-1.5 flex items-center justify-between gap-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          Слои
        </div>
        <button
          type="button"
          onClick={onThemeToggle}
          aria-label="Переключить тему"
          title={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
          className="rounded-full bg-slate-100 p-1.5 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
        >
          {theme === "dark" ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>
      <div className="space-y-1">
        {ITEMS.map(({ key, label }) => (
          <label
            key={key}
            className="flex cursor-pointer items-center gap-2 text-sm text-slate-700 dark:text-slate-300"
          >
            <input
              type="checkbox"
              checked={visibility[key]}
              onChange={() => onToggle(key)}
              className="h-4 w-4 rounded accent-emerald-500"
            />
            {label}
          </label>
        ))}
      </div>
    </div>
  );
}
