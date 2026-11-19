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

export type Percentiles = Partial<
  Record<"road" | "railway" | "construction" | "industrial", number>
>;

export type SelectedBuilding = {
  id: number;
  index: number | null;
  address: string | null;
  year: number | null;
  breakdown: BuildingBreakdown | null;
  /** Доля домов района, которые находятся дальше от объекта (тише), 0..1. */
  percentiles?: Percentiles;
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

const PERCENTILE_LABELS: Record<string, string> = {
  road: "близости к магистрали",
  railway: "близости к ж/д",
  construction: "близости к стройке",
  industrial: "близости к промзоне",
};

// Пороги презентации, не формулы: что считаем красным флагом и что —
// главной причиной. Баллы остаются в config/scoring.json.
export const RED_FLAG_PENALTY = -0.2;
export const SEVERE_PENALTY = -0.5;

// Публичный URL статического экспорта на GitHub Pages (basePath из next.config).
const PUBLIC_BASE = "https://montecristo1483.github.io/karta-okruzheniya";

function formatSigned(value: number): string {
  return `${value > 0 ? "+" : "−"}${Math.abs(value).toFixed(1)}`;
}

function distancePhrase(distance: number): string {
  return distance < 15 ? "рядом" : `в ${Math.round(distance)} м`;
}

function indexColor(index: number | null): string {
  if (index === null) return "#c9ced3";
  if (index < 5) return "#d98f86";
  if (index < 7) return "#c9bf9e";
  return "#7fb069";
}

function Ring({ index, size = 84 }: { index: number | null; size?: number }) {
  const stroke = size > 60 ? 9 : 7;
  const r = (size - stroke * 2 - 4) / 2;
  const c = 2 * Math.PI * r;
  const frac = index === null ? 0 : Math.max(0, Math.min(1, index / 10));
  const fsMain = size > 60 ? 22 : 13;
  const fsSub = size > 60 ? 10 : 7;
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
  contour,
  onContourChange,
}: {
  building: SelectedBuilding | null;
  onClose: () => void;
  contour: number | null;
  onContourChange: (minutes: number | null) => void;
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

  const breakdown = building?.breakdown ?? null;

  const penaltyEntries = Object.entries(breakdown?.penalties ?? {})
    .filter((entry): entry is [string, NonNullable<PenaltyEntry>] => entry[1] !== null)
    .sort((a, b) => a[1].penalty - b[1].penalty);

  const bonusEntries = Object.entries(breakdown?.bonuses ?? {})
    .filter((entry): entry is [string, NonNullable<BonusEntry>] => entry[1] !== null)
    .sort((a, b) => b[1].bonus - a[1].bonus);

  const redFlags = penaltyEntries.filter(([, entry]) => entry.penalty < RED_FLAG_PENALTY).slice(0, 3);
  const greenFacts = bonusEntries.filter(([, entry]) => entry.bonus > 0.05).slice(0, 3);

  // Бонус 0 — это не только «нет объекта», но и «дальше 15 минут».
  // Показываем такие категории отдельной осторожной строкой, чтобы
  // «школа 18 мин» не выглядела как «школы нет».
  const softMinuses = bonusEntries
    .filter(([, entry]) => entry.bonus < 0.05 && entry.minutes > 15)
    .slice(0, 2)
    .map(
      ([kind, entry]) =>
        `${BONUS_LABELS[kind] ?? kind} — ${Math.round(entry.minutes)} мин`,
    );

  const penaltyRows = penaltyEntries
    .filter(([, entry]) => entry.penalty < -0.005)
    .map(([key, entry]) => ({
      label: PENALTY_LABELS[key] ?? key,
      value: entry.penalty,
    }));

  const bonusRows = bonusEntries
    .filter(([, entry]) => entry.bonus > 0.005)
    .map(([key, entry]) => ({
      label: BONUS_LABELS[key] ?? key,
      value: entry.bonus,
    }));

  const severeFlags = redFlags.filter(([, entry]) => entry.penalty < SEVERE_PENALTY);

  const verdict = (() => {
    if (!building) return "";
    if (!breakdown) return "Недостаточно данных — проверьте окружение лично";
    if (severeFlags.length >= 2) {
      const [a, b] = severeFlags;
      return `Шумное место: ${PENALTY_LABELS[a[0]]?.toLowerCase() ?? a[0]} ${distancePhrase(a[1].distance_m)} и ${PENALTY_LABELS[b[0]]?.toLowerCase() ?? b[0]} ${distancePhrase(b[1].distance_m)}`;
    }
    if (severeFlags.length === 1) {
      const [kind, entry] = severeFlags[0];
      return `Главный минус: ${PENALTY_LABELS[kind]?.toLowerCase() ?? kind} ${distancePhrase(entry.distance_m)}`;
    }
    if (greenFacts.length >= 3) return "Спокойный дом с хорошей инфраструктурой";
    if (greenFacts.length >= 1) return "Есть плюсы, но проверьте детали";
    return "Мало данных — проверьте окружение лично";
  })();

  const worstPercentile = (Object.entries(building?.percentiles ?? {}) as [
    string,
    number | undefined,
  ][])
    .filter(
      ([kind, p]) =>
        typeof p === "number" &&
        p >= 0.6 &&
        redFlags.some(([k]) => k === kind),
    )
    .sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0))[0];

  const percentileLine = worstPercentile
    ? `По ${PERCENTILE_LABELS[worstPercentile[0]] ?? worstPercentile[0]} хуже ${Math.round(
        (worstPercentile[1] ?? 0) * 100,
      )}% домов Пресни`
    : null;

  const bonusByKey = Object.fromEntries(bonusEntries) as Record<
    string,
    NonNullable<BonusEntry>
  >;

  const checks: string[] = [];
  for (const [kind, entry] of redFlags) {
    if (kind === "road" && entry.distance_m < 200) {
      checks.push("Выйдите на балкон в час пик: слышно ли магистраль?");
    } else if (kind === "railway" && entry.distance_m < 300) {
      checks.push("Послушайте поезда вечером — ж/д рядом.");
    } else if (kind === "construction") {
      checks.push("Уточните, что строят и до какого года.");
    } else if (kind === "industrial") {
      checks.push("Проверьте, работает ли производство и есть ли выбросы.");
    }
  }
  if (bonusByKey.transport && bonusByKey.transport.bonus < 0.05) {
    checks.push("Пройдите маршрут до метро в час пик: в карточке больше 15 минут.");
  }
  if (bonusByKey.schools && bonusByKey.schools.bonus < 0.05) {
    checks.push("Проверьте, как ребёнок дойдёт до школы.");
  }
  if (checks.length === 0) checks.push("Посмотрите двор вечером и парковку.");

  const base = breakdown?.base ?? 5;
  const bonusSum = bonusEntries.reduce((sum, [, entry]) => sum + entry.bonus, 0);
  const penaltySum = penaltyEntries.reduce((sum, [, entry]) => sum + entry.penalty, 0);
  const rawTotal = base + bonusSum + penaltySum;
  const clamped =
    building?.index !== null &&
    building?.index !== undefined &&
    Math.abs(rawTotal - building.index) > 0.05;

  const share = async () => {
    if (!building) return;
    const url = `${PUBLIC_BASE}/?building=${building.id}`;
    const facts = redFlags
      .slice(0, 2)
      .map(
        ([kind, entry]) =>
          `${PENALTY_LABELS[kind]?.toLowerCase() ?? kind} ${distancePhrase(entry.distance_m)}`,
      )
      .join(", ");
    const text = `${building.address ?? "Дом"} — индекс окружения ${
      building.index?.toFixed(1) ?? "—"
    }/10${facts ? `. ${facts}` : ""}. ${url}`;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard недоступен");
      await navigator.clipboard.writeText(text);
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
                {building.year !== null && (
                  <div className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                    Год постройки — {building.year}
                    <span
                      className="ml-1 text-xs text-slate-400 dark:text-slate-500"
                      title="Источник: ГИС ЖКХ (dom.gosuslugi.ru)"
                    >
                      · ГИС ЖКХ
                    </span>
                  </div>
                )}
                <div className="mt-2">
                  <div className="mb-1 text-xs text-slate-400 dark:text-slate-500">
                    Зона пешком
                  </div>
                  <div className="flex rounded-full bg-slate-100 p-0.5 dark:bg-slate-800">
                    {[5, 10, 15].map((minutes) => {
                      const active = contour === minutes;
                      return (
                        <button
                          key={minutes}
                          type="button"
                          aria-pressed={active}
                          onClick={() => onContourChange(active ? null : minutes)}
                          className={`flex-1 rounded-full py-1 text-xs font-semibold transition-colors ${
                            active
                              ? "bg-emerald-500 text-white shadow-sm"
                              : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
                          }`}
                        >
                          {minutes} мин
                        </button>
                      );
                    })}
                  </div>
                </div>
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

              <div className="mt-3 flex items-start gap-3">
                <Ring index={building.index} />
                <div className="min-w-0 flex-1 pt-1">
                  <div className="text-xs text-slate-500 dark:text-slate-400">
                    Индекс окружения
                  </div>
                  <div className="mt-0.5 text-sm font-semibold leading-snug text-slate-800 dark:text-slate-100">
                    {verdict}
                  </div>
                  {percentileLine && (
                    <div className="mt-1 text-xs leading-snug text-slate-500 dark:text-slate-400">
                      {percentileLine}
                    </div>
                  )}
                  {breakdown && (
                    <div className="mt-1 text-[11px] leading-tight text-slate-400 dark:text-slate-500">
                      Из чего: {base.toFixed(1)} + {bonusSum.toFixed(1)} −{" "}
                      {Math.abs(penaltySum).toFixed(1)} = {rawTotal.toFixed(1)}
                      {clamped && building.index !== null
                        ? ` → ${building.index.toFixed(1)}`
                        : ""}
                    </div>
                  )}
                </div>
              </div>

              {redFlags.length > 0 ? (
                <div className="mt-3 rounded-2xl bg-red-50 px-4 py-3 dark:bg-red-950/50">
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
                    Главные минусы
                  </div>
                  <ul className="space-y-1">
                    {redFlags.map(([kind, entry]) => (
                      <li
                        key={kind}
                        className="flex items-baseline justify-between gap-3 text-sm text-red-900/80 dark:text-red-200/80"
                      >
                        <span>{PENALTY_LABELS[kind] ?? kind}</span>
                        <span className="shrink-0 font-semibold">
                          {distancePhrase(entry.distance_m)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <div className="mt-3 rounded-2xl bg-emerald-50 px-4 py-3 text-sm text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
                  Явных красных флагов рядом нет — но проверьте лично.
                </div>
              )}

              {greenFacts.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {greenFacts.map(([kind, entry]) => (
                    <span
                      key={kind}
                      className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200"
                    >
                      {BONUS_LABELS[kind] ?? kind} · {Math.round(entry.minutes)} мин
                    </span>
                  ))}
                </div>
              )}

              {softMinuses.length > 0 && (
                <div className="mt-2 text-xs leading-snug text-amber-700 dark:text-amber-300">
                  Далеко: {softMinuses.join(" · ")}
                </div>
              )}

              <div className="mt-3 rounded-2xl bg-slate-50 px-4 py-3 dark:bg-slate-800/60">
                <div className="mb-1.5 text-sm font-bold text-slate-700 dark:text-slate-200">
                  Что проверить лично
                </div>
                <ul className="list-disc space-y-1 pl-4 text-sm text-slate-600 dark:text-slate-300">
                  {checks.slice(0, 3).map((check) => (
                    <li key={check}>{check}</li>
                  ))}
                </ul>
              </div>

              {breakdown && (
                <details className="mt-3 rounded-2xl bg-slate-50 px-4 py-3 dark:bg-slate-800/60">
                  <summary className="cursor-pointer text-sm font-semibold text-slate-700 dark:text-slate-200">
                    Почему такой балл
                  </summary>
                  <div className="mt-3 space-y-3">
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
                    <div className="border-t border-slate-200 pt-2 text-xs leading-relaxed text-slate-500 dark:border-slate-700 dark:text-slate-400">
                      База {base.toFixed(1)} + бонусы {bonusSum.toFixed(1)} − штрафы{" "}
                      {Math.abs(penaltySum).toFixed(1)} = {rawTotal.toFixed(1)}
                      {clamped && building.index !== null && (
                        <span className="font-semibold text-slate-700 dark:text-slate-200">
                          {" "}
                          → ограничено {building.index.toFixed(1)} (шкала 0…10)
                        </span>
                      )}
                    </div>
                  </div>
                </details>
              )}

              <button
                type="button"
                onClick={() => void share()}
                className="mt-4 w-full rounded-2xl bg-emerald-500 py-3.5 text-base font-semibold text-white transition-colors hover:bg-emerald-600 active:bg-emerald-700"
              >
                Поделиться
              </button>
              {notice === "copied" && (
                <div className="fixed bottom-6 left-1/2 z-20 -translate-x-1/2 rounded-full bg-slate-800 px-4 py-2 text-sm font-medium text-white shadow-lg dark:bg-slate-100 dark:text-slate-800">
                  Ссылка и факты скопированы
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
