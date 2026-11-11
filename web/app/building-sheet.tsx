"use client";

import { useRef } from "react";

export type BonusEntry = {
  distance_m: number;
  minutes: number;
  multiplier: number;
  bonus: number;
} | null;

export type PenaltyEntry = {
  distance_m: number;
  class: string;
  weight: number;
  penalty: number;
} | null;

export type BuildingBreakdown = {
  base: number;
  index: number;
  bonuses: Record<string, BonusEntry>;
  penalties: Record<string, PenaltyEntry>;
};

export type SelectedBuilding = {
  id: number;
  index: number | null;
  address: string | null;
  breakdown: BuildingBreakdown | null;
};

const BONUS_LABELS: Record<string, string> = {
  transport: "Транспорт",
  schools: "Школы",
  parks: "Парки",
  kindergartens: "Детские сады",
  shops: "Магазины",
};

const PENALTY_LABELS: Record<string, string> = {
  road: "Магистраль",
  railway: "Ж/д",
  construction: "Стройка",
  industrial: "Промзона",
};

function formatSigned(value: number): string {
  return `${value > 0 ? "+" : "−"}${Math.abs(value).toFixed(1)}`;
}

function indexColor(index: number | null): string {
  if (index === null) return "#c9ced3";
  if (index < 5) return "#d98f86";
  if (index < 7) return "#c9bf9e";
  return "#7fb069";
}

function Ring({ index }: { index: number | null }) {
  const r = 40;
  const c = 2 * Math.PI * r;
  const frac = index === null ? 0 : Math.max(0, Math.min(1, index / 10));
  return (
    <svg width="104" height="104" viewBox="0 0 104 104" className="shrink-0">
      <circle cx="52" cy="52" r={r} fill="none" stroke="#e2e8f0" strokeOpacity="0.8" strokeWidth="10" className="dark:stroke-slate-700 dark:stroke-opacity-40" />
      <circle
        cx="52"
        cy="52"
        r={r}
        fill="none"
        stroke={indexColor(index)}
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray={`${frac * c} ${c}`}
        transform="rotate(-90 52 52)"
      />
      <text x="52" y="50" textAnchor="middle" className="fill-slate-800 dark:fill-slate-100" fontSize="26" fontWeight="700">
        {index === null ? "—" : index.toFixed(1)}
      </text>
      <text x="52" y="68" textAnchor="middle" className="fill-slate-400 dark:fill-slate-500" fontSize="12">
        / 10
      </text>
    </svg>
  );
}

function Bars({
  title,
  rows,
  colorClass,
  trackValue,
}: {
  title: string;
  rows: { label: string; value: number }[];
  colorClass: string;
  trackValue: (v: number) => string;
}) {
  if (!rows.length) return null;
  const max = Math.max(...rows.map((r) => Math.abs(r.value)));
  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
        {title}
      </div>
      <div className="space-y-1.5">
        {rows.map((row) => (
          <div key={row.label} className="flex items-center gap-2">
            <div className="w-28 shrink-0 truncate text-sm text-slate-700 dark:text-slate-300">
              {row.label}
            </div>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
              <div
                className={`h-full rounded-full ${colorClass}`}
                style={{ width: `${(Math.abs(row.value) / max) * 100}%` }}
              />
            </div>
            <div className={`w-11 shrink-0 text-right text-sm font-semibold ${colorClass.replace("bg-", "text-")}`}>
              {trackValue(row.value)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function BuildingSheet({
  building,
  onClose,
}: {
  building: SelectedBuilding | null;
  onClose: () => void;
}) {
  const touchStartY = useRef<number | null>(null);
  const breakdown = building?.breakdown ?? null;
  const bonusRows = Object.entries(breakdown?.bonuses ?? {})
    .map(([key, entry]) => ({
      label: BONUS_LABELS[key] ?? key,
      value: entry?.bonus ?? 0,
    }))
    .filter((row) => row.value > 0.005);
  const penaltyRows = Object.entries(breakdown?.penalties ?? {})
    .map(([key, entry]) => ({
      label: PENALTY_LABELS[key] ?? key,
      value: entry?.penalty ?? 0,
    }))
    .filter((row) => row.value < -0.005);
  const facts = Object.entries(breakdown?.penalties ?? {})
    .map(([key, entry]) => {
      if (!entry || entry.penalty >= -0.005) return null;
      const label = PENALTY_LABELS[key] ?? key;
      if (entry.distance_m == null) return label;
      const d = Math.round(entry.distance_m);
      return d < 15 ? `${label} рядом` : `${label} в ${d} м`;
    })
    .filter((fact): fact is string => fact !== null);

  return (
    <div
      className={`pointer-events-none fixed inset-x-0 bottom-0 z-10 transition-transform duration-300 ease-out ${
        building ? "translate-y-0" : "translate-y-full"
      }`}
    >
      <div
        id="building-sheet"
        className="pointer-events-auto mx-auto w-full max-w-md rounded-t-3xl bg-white/95 px-5 pb-5 pt-3 shadow-2xl backdrop-blur dark:bg-slate-900/95"
      >
        <div
          className="mx-auto mb-3 h-1.5 w-12 touch-none rounded-full bg-slate-200 dark:bg-slate-700"
          onTouchStart={(e) => {
            touchStartY.current = e.touches[0]?.clientY ?? null;
          }}
          onTouchEnd={(e) => {
            const start = touchStartY.current;
            touchStartY.current = null;
            const end = e.changedTouches[0]?.clientY;
            if (start != null && end != null && end - start > 60) onClose();
          }}
        />
        {building && (
          <>
            <div className="relative">
              <h2 className="truncate pr-8 text-lg font-bold text-slate-800 dark:text-slate-100">
                {building.address ?? "Адрес не указан"}
              </h2>
              <button
                type="button"
                onClick={onClose}
                aria-label="Закрыть"
                className="absolute -top-1 right-0 rounded-full bg-slate-100 p-1.5 text-slate-500 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
              >
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
                  <path
                    d="M1 1l12 12M13 1L1 13"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            </div>

            <div className="mt-3 flex items-center gap-4">
              <Ring index={building.index} />
              <div className="min-w-0 flex-1 space-y-3">
                <div className="text-xs text-slate-500 dark:text-slate-400">
                  Индекс окружения
                </div>
                <Bars
                  title="Бонусы"
                  rows={bonusRows}
                  colorClass="bg-emerald-400"
                  trackValue={(v) => formatSigned(v)}
                />
                <Bars
                  title="Штрафы"
                  rows={penaltyRows}
                  colorClass="bg-red-400"
                  trackValue={(v) => formatSigned(v)}
                />
              </div>
            </div>

            <div className="mt-4 flex rounded-xl bg-slate-100 p-1 text-sm font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
              {["5 мин", "10 мин", "15 мин"].map((label, i) => (
                <button
                  key={label}
                  type="button"
                  disabled
                  title="Изохроны появятся в v1.1"
                  className={`flex-1 rounded-lg py-2 ${
                    i === 1 ? "bg-white text-slate-800 shadow dark:bg-slate-700 dark:text-slate-100" : ""
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {facts.length > 0 && (
              <div className="mt-4 rounded-2xl bg-red-50 px-4 py-3 dark:bg-red-950/50">
                <div className="mb-1.5 flex items-center gap-2 text-sm font-bold text-red-700 dark:text-red-300">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
                    <path
                      d="M8 1.5 15 14H1L8 1.5Z"
                      stroke="currentColor"
                      strokeWidth="1.4"
                      strokeLinejoin="round"
                    />
                    <path d="M8 6.5v3.2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                    <circle cx="8" cy="11.6" r="0.9" fill="currentColor" />
                  </svg>
                  Подводные камни
                </div>
                <ul className="space-y-1">
                  {facts.map((fact) => (
                    <li key={fact} className="text-sm text-red-900/80 dark:text-red-200/80">
                      {fact}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <button
              type="button"
              disabled
              title="Появится в M5"
              className="mt-4 w-full rounded-2xl bg-emerald-500 py-3.5 text-base font-semibold text-white opacity-50"
            >
              Отправить клиенту
            </button>
            <p className="mt-2 text-center text-[11px] leading-tight text-slate-400">
              Справочная информация по открытым данным, не является замером или оценкой
              стоимости
            </p>
          </>
        )}
      </div>
    </div>
  );
}
