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

export default function LayersPanel({
  visibility,
  onToggle,
}: {
  visibility: Visibility;
  onToggle: (key: LayerKey) => void;
}) {
  return (
    <div className="absolute left-3 top-3 z-10 rounded-2xl bg-white/90 p-3 shadow-lg backdrop-blur">
      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
        Слои
      </div>
      <div className="space-y-1">
        {ITEMS.map(({ key, label }) => (
          <label
            key={key}
            className="flex cursor-pointer items-center gap-2 text-sm text-slate-700"
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
