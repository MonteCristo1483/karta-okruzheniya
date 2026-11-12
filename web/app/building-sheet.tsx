"use client";

import { useEffect, useRef, useState } from "react";

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

// Публичный URL статического экспорта на GitHub Pages (basePath из next.config).
const PUBLIC_BASE = "https://montecristo1483.github.io/karta-okruzheniya";

function formatSigned(value: number): string {
  return `${value > 0 ? "+" : "−"}${Math.abs(value).toFixed(1)}`;
}

function indexColor(index: number | null): string {
  if (index === null) return "#c9ced3";
  if (index < 5) return "#d98f86";
  if (index < 7) return "#c9bf9e";
  return "#7fb069";
}

function Ring({ index, size = 104 }: { index: number | null; size?: number }) {
  const stroke = size > 60 ? 10 : 8;
  const r = (size - stroke * 2 - 4) / 2;
  const c = 2 * Math.PI * r;
  const frac = index === null ? 0 : Math.max(0, Math.min(1, index / 10));
  const fsMain = size > 60 ? 26 : 14;
  const fsSub = size > 60 ? 12 : 8;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e2e8f0" strokeOpacity="0.8" strokeWidth={stroke} className="dark:stroke-slate-700 dark:stroke-opacity-40" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={indexColor(index)}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={`${frac * c} ${c}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text x="50%" y="48%" textAnchor="middle" dominantBaseline="middle" className="fill-slate-800 dark:fill-slate-100" fontSize={fsMain} fontWeight="700">
        {index === null ? "—" : index.toFixed(1)}
      </text>
      <text x="50%" y="66%" textAnchor="middle" className="fill-slate-400 dark:fill-slate-500" fontSize={fsSub}>
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

type Snap = "min" | "mid" | "max";

export default function BuildingSheet({
  building,
  onClose,
}: {
  building: SelectedBuilding | null;
  onClose: () => void;
}) {
  const touchStartY = useRef<number | null>(null);
  const [snap, setSnap] = useState<Snap>("mid");
  const [notice, setNotice] = useState<"idle" | "copied" | "manual">("idle");
  const [shareUrl, setShareUrl] = useState("");
  const noticeTimer = useRef<number | null>(null);

  // Новый выбор — шторка в среднее положение.
  useEffect(() => {
    setSnap("mid");
  }, [building?.id]);

  const share = async () => {
    if (!building) return;
    const url = `${PUBLIC_BASE}/?building=${building.id}`;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard недоступен");
      await navigator.clipboard.writeText(url);
      if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
      setNotice("copied");
      noticeTimer.current = window.setTimeout(() => setNotice("idle"), 2500);
    } catch {
      // http без secure-контекста (например, по IP в локальной сети) —
      // буфер обмена недоступен, показываем ссылку для ручного копирования.
      setShareUrl(url);
      setNotice("manual");
    }
  };

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

  // Мобильные высоты (<768px): 45% по умолчанию, свайп вверх → 85%,
  // свайп вниз → минимальный вид. Десктоп (md:) — как раньше, по контенту.
  const heightClass =
    snap === "min"
      ? "h-[92px]"
      : snap === "max"
        ? "h-[85dvh]"
        : "h-[45dvh]";

  const swipe = (dy: number) => {
    if (dy > 60) setSnap((s) => (s === "max" ? "mid" : "min"));
    else if (dy < -60) setSnap((s) => (s === "min" ? "mid" : "max"));
  };

  return (
    <div
      className={`pointer-events-none fixed inset-x-0 bottom-0 z-10 transition-[height,transform] duration-300 ease-out ${heightClass} md:h-auto ${
        building ? "translate-y-0" : "translate-y-full"
      }`}
    >
      <div
        id="building-sheet"
        className="pointer-events-auto mx-auto flex h-full w-full max-w-md flex-col rounded-t-3xl bg-white/95 shadow-2xl backdrop-blur dark:bg-slate-900/95 md:h-auto"
      >
        <div
          className="mx-auto mb-1 mt-2 h-1.5 w-12 shrink-0 touch-none rounded-full bg-slate-200 dark:bg-slate-700"
          onTouchStart={(e) => {
            touchStartY.current = e.touches[0]?.clientY ?? null;
          }}
          onTouchEnd={(e) => {
            const start = touchStartY.current;
            touchStartY.current = null;
            const end = e.changedTouches[0]?.clientY;
            if (start != null && end != null) swipe(end - start);
          }}
        />
        {building && (
          <>
            {/* Минимальный вид (мобильный): адрес + кольцо, тап — развернуть */}
            <button
              type="button"
              onClick={() => setSnap("mid")}
              className={`flex shrink-0 items-center gap-3 px-5 pb-2 text-left md:hidden ${snap === "min" ? "" : "hidden"}`}
            >
              <span className="min-w-0 flex-1 truncate text-base font-bold text-slate-800 dark:text-slate-100">
                {building.address ?? "Адрес не указан"}
              </span>
              <Ring index={building.index} size={44} />
            </button>

            {/* Полный вид: скрыт в минимальном на мобильном */}
            <div
              className={`min-h-0 flex-1 overflow-y-auto px-5 pb-5 md:block md:overflow-visible ${snap === "min" ? "hidden" : ""}`}
            >
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
                onClick={() => void share()}
                className="mt-4 w-full rounded-2xl bg-emerald-500 py-3.5 text-base font-semibold text-white transition-colors hover:bg-emerald-600 active:bg-emerald-700"
              >
                Отправить клиенту
              </button>
              {notice === "copied" && (
                <div className="fixed bottom-6 left-1/2 z-20 -translate-x-1/2 rounded-full bg-slate-800 px-4 py-2 text-sm font-medium text-white shadow-lg dark:bg-slate-100 dark:text-slate-800">
                  Ссылка скопирована
                </div>
              )}
              {notice === "manual" && (
                <div className="mt-3 rounded-2xl bg-amber-50 px-4 py-3 dark:bg-amber-950/50">
                  <p className="text-sm font-medium text-amber-800 dark:text-amber-200">
                    Автокопирование недоступно в этом браузере — скопируйте ссылку вручную:
                  </p>
                  <input
                    readOnly
                    value={shareUrl}
                    onFocus={(e) => e.target.select()}
                    className="mt-2 w-full rounded-lg border border-amber-200 bg-white px-2 py-1.5 font-mono text-xs text-slate-700 dark:border-amber-900 dark:bg-slate-900 dark:text-slate-200"
                  />
                </div>
              )}
              <p className="mt-2 text-center text-[11px] leading-tight text-slate-400">
                Справочная информация по открытым данным, не является замером или оценкой
                стоимости
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
