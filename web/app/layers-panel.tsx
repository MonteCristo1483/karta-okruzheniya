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

const SHORT_LABELS: Partial<Record<LayerKey, string>> = {
  metro: "Метро",
  redzones: "Красные зоны",
};

const ITEMS: { key: LayerKey; label: string; icon: IconKey }[] = [
  { key: "schools", label: "Школы", icon: "school" },
  { key: "kindergartens", label: "Сады", icon: "kindergarten" },
  { key: "parks", label: "Парки", icon: "park" },
  { key: "metro", label: "Метро и электрички", icon: "subway_entrance" },
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

function CheckIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden>
      <path
        d="M2 6.3 4.7 9 10 3.2"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
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
    <div className="absolute left-3 top-3 z-10 flex flex-col items-start gap-1.5 md:top-3 md:rounded-2xl md:bg-white/90 md:p-2.5 md:shadow-lg md:backdrop-blur dark:md:bg-slate-900/90">
      <div className="flex items-center gap-3 md:mb-1 md:w-full md:justify-between">
        <div className="hidden items-center gap-1.5 md:flex">
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
          className="flex h-8 w-8 items-center justify-center rounded-full bg-white/85 p-1.5 text-slate-600 shadow-sm backdrop-blur hover:bg-white dark:bg-slate-900/80 dark:text-slate-300 dark:hover:bg-slate-800 md:h-auto md:w-auto md:bg-slate-100 md:shadow-none dark:md:bg-slate-800"
        >
          {theme === "dark" ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>
      <div className="flex flex-col items-start gap-1.5 md:w-full md:gap-0 md:space-y-0.5">
        {ITEMS.map(({ key, label, icon }) => {
          const active = visibility[key];
          return (
            <label
              key={key}
              className={`flex cursor-pointer items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium shadow-sm backdrop-blur transition-colors md:w-full md:rounded-none md:bg-transparent md:p-0 md:text-[13px] md:font-normal md:shadow-none md:backdrop-blur-none ${
                active
                  ? "bg-white/85 text-slate-700 dark:bg-slate-900/80 dark:text-slate-200"
                  : "bg-white/55 text-slate-500 dark:bg-slate-900/45 dark:text-slate-400"
              }`}
            >
              <input
                type="checkbox"
                checked={active}
                onChange={() => onToggle(key)}
                className="hidden h-3.5 w-3.5 rounded accent-emerald-500 md:block"
              />
              <span
                className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border md:hidden ${
                  active
                    ? "border-emerald-500 bg-emerald-500 text-white"
                    : "border-slate-300 bg-white/60 text-transparent dark:border-slate-600 dark:bg-slate-800/60"
                }`}
                aria-hidden
              >
                <CheckIcon />
              </span>
              <LayerIcon d={ICON_PATHS[icon]} />
              <span className="md:hidden">{SHORT_LABELS[key] ?? label}</span>
              <span className="hidden md:inline">{label}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
