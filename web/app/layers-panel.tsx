import type { Theme } from "./theme";
import { ICON_PATHS, type IconKey } from "./icons";

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
  routes: false,
};

const ITEMS: { key: LayerKey; label: string; icon: IconKey }[] = [
  { key: "schools", label: "Школы", icon: "school" },
  { key: "kindergartens", label: "Сады", icon: "kindergarten" },
  { key: "parks", label: "Парки", icon: "park" },
  { key: "metro", label: "Метро", icon: "subway_entrance" },
  { key: "shops", label: "Магазины", icon: "shop" },
  { key: "redzones", label: "Красные зоны", icon: "redzones" },
  { key: "routes", label: "Маршруты", icon: "routes" },
];

function LayerIcon({ d }: { d: string }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0 text-slate-400 dark:text-slate-500"
      aria-hidden
    >
      <path d={d} />
    </svg>
  );
}

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
    <div className="absolute left-3 top-3 z-10 rounded-2xl bg-white/90 p-2.5 shadow-lg backdrop-blur dark:bg-slate-900/90">
      <div className="mb-1 flex items-center justify-between gap-3">
        <div className="flex items-center gap-1.5">
          <svg width="15" height="15" viewBox="0 0 512 512" aria-hidden>
            <circle cx="256" cy="256" r="130" fill="none" stroke="#6a9d76" strokeWidth="64" />
          </svg>
          <span className="text-[13px] font-bold text-slate-700 dark:text-slate-200">
            Карта окружения
          </span>
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
      <div className="space-y-0.5">
        {ITEMS.map(({ key, label, icon }) => (
          <label
            key={key}
            className="flex cursor-pointer items-center gap-1.5 text-[13px] text-slate-700 dark:text-slate-300"
          >
            <input
              type="checkbox"
              checked={visibility[key]}
              onChange={() => onToggle(key)}
              className="h-3.5 w-3.5 rounded accent-emerald-500"
            />
            <LayerIcon d={ICON_PATHS[icon]} />
            {label}
          </label>
        ))}
      </div>
    </div>
  );
}
