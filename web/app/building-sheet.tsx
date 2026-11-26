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
  v?: number;
  i?: number | null;
  /** 7 факторов 0..1: транспорт, промзоны, доступность, зелень, дом, плотность, ветер. */
  f?: (number | null)[];
  fl?: string[];
  base?: number;
  index?: number;
  bonuses?: Record<string, BonusEntry>;
  penalties?: Record<string, PenaltyEntry>;
};

export function isCompactBreakdown(b: BuildingBreakdown | null): b is BuildingBreakdown & { v: number; f: (number | null)[] } {
  return b !== null && typeof b.v === "number" && Array.isArray(b.f);
}

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

// Пороги вердикта — тоже презентация: балл считает пайплайн, карточка
// только называет уровень.
export const VERDICT_HIGH = 7.0;
export const VERDICT_MID = 5.0;
// v2: порядок и веса факторов — только для сортировки причин, не для балла.
const V2_FACTORS = [
  { key: "transport", label: "Транспорт", weight: 27, sign: -1, high: "Транспортная нагрузка высокая", mid: "Транспортный фон умеренный", low: "Тихий двор вдали от магистралей" },
  { key: "industry", label: "Промышленность", weight: 18, sign: -1, high: "Промышленный источник рядом", mid: "Промышленный фон умеренный", low: "Промышленных источников рядом нет" },
  { key: "access", label: "Доступность", weight: 18, sign: 1, high: "Метро и инфраструктура в пешей доступности", mid: "Часть инфраструктуры в пешей доступности", low: "До метро и инфраструктуры далеко" },
  { key: "green", label: "Зелень", weight: 13.5, sign: 1, high: "Парк в пешей доступности", mid: "Зелени рядом немного", low: "Зелёных зон рядом нет" },
  { key: "home", label: "Дом", weight: 9, sign: 1, high: "Дом новый или современной серии", mid: "Дом средней постройки", low: "Дом старый — панель до 1970" },
  { key: "density", label: "Плотность", weight: 4.5, sign: -1, high: "Плотная застройка", mid: "Плотность застройки средняя", low: "Свободная застройка, мало соседей" },
  { key: "wind", label: "Ветер", weight: 10, sign: -1, high: "Ветер часто со стороны предприятия", mid: "Ветер иногда со стороны предприятия", low: "Ветер редко приносит выбросы" },
] as const;
const V2_FLAG_LETTERS: Record<string, string> = { t: "transport", i: "industry", a: "access", g: "green", h: "home", d: "density", w: "wind" };
const V2_MISSING: Record<string, string> = { transport: "нет данных по транспорту", industry: "промышленный источник не классифицирован", access: "доступность не рассчитана", green: "нет площади парков", home: "нет данных о доме", density: "нет данных по плотности", wind: "нет данных по ветру" };
function v2Verdict(score: number | null): string {
  if (score === null) return "Оценка недоступна";
  if (score >= VERDICT_HIGH) return "Выше среднего по Москве";
  if (score >= VERDICT_MID) return "Средний уровень по Москве";
  return "Ниже среднего по Москве";
}
function v2Reasons(compact: BuildingBreakdown, year: number | null) {
  const missing = new Map<string, string>();
  for (const flag of compact.fl ?? []) {
    const [letter, ...parts] = flag.split(":");
    const factor = V2_FLAG_LETTERS[letter];
    if (factor && parts.length > 0) {
      missing.set(factor, parts[parts.length - 1] === "us" ? "статус предприятия неизвестен" : V2_MISSING[factor]);
    }
  }
  if (year === null) missing.set("home", "нет года постройки");
  const reasons = V2_FACTORS.map((factor, index) => {
    const raw = compact.f?.[index];
    const value = typeof raw === "number" ? raw : null;
    const gap = missing.has(factor.key);
    const level = value === null ? null : value >= 0.55 ? "high" : value <= 0.25 ? "low" : "mid";
    const good = level === "high" ? factor.sign > 0 : level === "low" ? factor.sign < 0 : false;
    return {
      key: factor.key,
      gap,
      impact: value === null ? 0 : Math.abs(value * factor.weight),
      tone: gap || level === null ? "neutral" : level === "mid" ? "neutral" : good ? "good" : "bad",
      text: gap ? `${factor.label}: данных нет` : level ? factor[level] : `${factor.label}: данных нет`,
    };
  })
    .sort((a, b) => Number(a.gap) - Number(b.gap) || b.impact - a.impact).slice(0, 3);
  return { reasons, missing: Array.from(new Set(missing.values())) };
}

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

function ScoreChip({
  label,
  value,
  max,
}: {
  label: string;
  value: number;
  max: number;
}) {
  const positive = value > 0;
  const width = Math.max(6, Math.min(100, (Math.abs(value) / max) * 100));
  return (
    <div
      className={`rounded-xl px-2 py-1.5 ${
        positive
          ? "bg-emerald-50 dark:bg-emerald-950/40"
          : "bg-red-50 dark:bg-red-950/50"
      }`}
    >
      <div className="flex items-baseline justify-between gap-1">
        <span
          className={`truncate text-[11px] font-medium ${
            positive
              ? "text-emerald-900 dark:text-emerald-200"
              : "text-red-900/80 dark:text-red-200/80"
          }`}
        >
          {label}
        </span>
        <span
          className={`shrink-0 text-[11px] font-bold ${
            positive
              ? "text-emerald-700 dark:text-emerald-300"
              : "text-red-700 dark:text-red-300"
          }`}
        >
          {formatSigned(value)}
        </span>
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded-full bg-black/5 dark:bg-white/10">
        <div
          className={`h-full rounded-full ${
            positive ? "bg-emerald-400" : "bg-red-400"
          }`}
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}

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
  const compact = isCompactBreakdown(breakdown) ? breakdown : null;
  const score = compact?.i ?? building?.index ?? null;
  const compactInfo = compact && building ? v2Reasons(compact, building.year) : null;

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

  // Компактная разбивка: единая сетка плашек ± в 2 колонки.
  const visibleBonuses = bonusEntries.filter(([, entry]) => entry.bonus > 0.005);
  const visiblePenalties = penaltyEntries.filter(([, entry]) => entry.penalty < -0.005);
  const bonusChips = visibleBonuses.slice(0, 3).map(([key, entry]) => ({
    key: `b-${key}`,
    label: BONUS_LABELS[key] ?? key,
    value: entry.bonus,
  }));
  const penaltyChips = visiblePenalties.slice(0, 3).map(([key, entry]) => ({
    key: `p-${key}`,
    label: PENALTY_LABELS[key] ?? key,
    value: entry.penalty,
  }));
  const bonusMax = Math.max(1, ...bonusChips.map((item) => Math.abs(item.value)));
  const penaltyMax = Math.max(1, ...penaltyChips.map((item) => Math.abs(item.value)));
  const scoreChips = [
    ...bonusChips.map((item) => ({ ...item, max: bonusMax })),
    ...penaltyChips.map((item) => ({ ...item, max: penaltyMax })),
  ];
  const hiddenCount =
    Math.max(0, visibleBonuses.length - 3) +
    Math.max(0, visiblePenalties.length - 3);

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
    const url = `${PUBLIC_BASE}/r/${building.id}/`;
    const facts = redFlags
      .slice(0, 2)
      .map(
        ([kind, entry]) =>
          `${PENALTY_LABELS[kind]?.toLowerCase() ?? kind} ${distancePhrase(entry.distance_m)}`,
      )
      .join(", ");
    const text = `${building.address ?? "Дом"} — индекс окружения ${
      score?.toFixed(1) ?? "—"
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
  // свайп вниз → минимальный вид. Десктоп (md:) — правый док фиксированной
  // ширины во всю высоту экрана, скролл внутри панели.
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
      className={`pointer-events-none fixed inset-x-0 bottom-0 z-10 transition-[height,transform] duration-300 ease-out ${heightClass} md:left-auto md:inset-y-4 md:right-4 md:h-auto md:w-[clamp(340px,38vw,420px)] xl:w-[420px] ${
        building
          ? "translate-y-0"
          : "translate-y-full md:translate-y-0 md:translate-x-[calc(100%+32px)]"
      }`}
    >
      <div
        id="building-sheet"
        data-sheet={building ? "open" : "closed"}
        className="pointer-events-auto mx-auto flex h-full w-full max-w-md flex-col rounded-t-3xl bg-white/95 shadow-2xl backdrop-blur dark:bg-slate-900/95 md:absolute md:inset-0 md:mx-0 md:h-auto md:max-w-none md:rounded-2xl"
      >
        <div
          className="mx-auto mb-1 mt-2 h-1.5 w-12 shrink-0 touch-none rounded-full bg-slate-200 dark:bg-slate-700 md:hidden"
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
              <Ring index={score} size={44} />
            </button>

            {/* Полный вид: скрыт в минимальном на мобильном */}
            <div
              className={`min-h-0 flex-1 overflow-y-auto px-5 pb-5 md:block md:overflow-y-auto md:pt-4 ${snap === "min" ? "hidden" : ""}`}
            >
              <div className="relative flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <h2 className="truncate text-lg font-bold text-slate-800 dark:text-slate-100">
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
                  <div className="mt-1.5 text-lg font-bold leading-tight text-slate-800 dark:text-slate-100">
                    {compact ? v2Verdict(score) : "Индекс окружения"}
                  </div>
                  <div className="mt-0.5 text-sm font-semibold leading-snug text-slate-800 dark:text-slate-100">
                    {compact ? `Индекс окружения — ${score === null ? "—" : score.toFixed(1)} из 10` : verdict}
                  </div>
                  {percentileLine && (
                    <div className="mt-1 text-xs leading-snug text-slate-500 dark:text-slate-400">
                      {percentileLine}
                    </div>
                  )}
                </div>
                <div className="relative shrink-0 pt-2">
                  <Ring index={score} />
                  <button
                    type="button"
                    onClick={onClose}
                    aria-label="Закрыть"
                    className="absolute -right-1 -top-1 rounded-full bg-slate-100 p-1.5 text-slate-500 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
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
              </div>

              {compactInfo && (
                <div className="mt-3">
                  <ul className="space-y-1.5">
                    {compactInfo.reasons.map((reason) => (
                      <li key={reason.key} className="flex items-start gap-2 text-sm leading-snug text-slate-700 dark:text-slate-200">
                        <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${reason.tone === "good" ? "bg-emerald-400" : reason.tone === "bad" ? "bg-red-400" : "bg-slate-300 dark:bg-slate-600"}`} />
                        <span>{reason.text}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-2 text-[11px] leading-tight text-slate-400 dark:text-slate-500">
                    {compactInfo.missing.length > 0 ? `Данные: ${compactInfo.missing.join(" · ")}` : "Данные по дому и окружению полные"}
                  </div>
                </div>
              )}

              {!compact && scoreChips.length > 0 && (
                <div className="mt-3 grid grid-cols-2 gap-1.5">
                  {scoreChips.map((item) => (
                    <ScoreChip
                      key={item.key}
                      label={item.label}
                      value={item.value}
                      max={item.max}
                    />
                  ))}
                </div>
              )}
              {!compact && hiddenCount > 0 && (
                <div className="mt-1 text-[10px] text-slate-400 dark:text-slate-500">
                  Ещё {hiddenCount} — в «Почему такой балл»
                </div>
              )}

              <div className="mt-3">
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

              {!compact && breakdown && (
                <details className="group mt-3 rounded-2xl bg-slate-50 px-4 py-3 dark:bg-slate-800/60">
                  <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-semibold text-slate-700 dark:text-slate-200 [&::-webkit-details-marker]:hidden">
                    <span>Почему такой балл</span>
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 14 14"
                      fill="none"
                      aria-hidden
                      className="shrink-0 text-slate-400 transition-transform group-open:rotate-180"
                    >
                      <path
                        d="M3.5 5.25 7 8.75l3.5-3.5"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </summary>
                  <div className="mt-3 space-y-3">
                    <div className="text-[11px] leading-tight text-slate-400 dark:text-slate-500">
                      Из чего: {base.toFixed(1)} + {bonusSum.toFixed(1)} −{" "}
                      {Math.abs(penaltySum).toFixed(1)} = {rawTotal.toFixed(1)}
                      {clamped && building.index !== null
                        ? ` → ${building.index.toFixed(1)}`
                        : ""}
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

              {!compact && (redFlags.length > 0 ? (
                <details className="group mt-3 rounded-2xl bg-red-50 px-4 py-3 dark:bg-red-950/50">
                  <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-bold text-red-700 dark:text-red-300 [&::-webkit-details-marker]:hidden">
                    <span className="flex items-center gap-2">
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
                    </span>
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 14 14"
                      fill="none"
                      aria-hidden
                      className="shrink-0 text-red-500/80 transition-transform group-open:rotate-180 dark:text-red-300/80"
                    >
                      <path
                        d="M3.5 5.25 7 8.75l3.5-3.5"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </summary>
                  <ul className="mt-2 space-y-1">
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
                </details>
              ) : (
                <div className="mt-3 rounded-2xl bg-emerald-50 px-4 py-3 text-sm text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
                  Явных красных флагов рядом нет — но проверьте лично.
                </div>
              ))}

              {!compact && greenFacts.length > 0 && (
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

              {!compact && softMinuses.length > 0 && (
                <div className="mt-2 text-xs leading-snug text-amber-700 dark:text-amber-300">
                  Далеко: {softMinuses.join(" · ")}
                </div>
              )}

              <details className="group mt-3 rounded-2xl bg-slate-50 px-4 py-3 dark:bg-slate-800/60">
                <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-bold text-slate-700 dark:text-slate-200 [&::-webkit-details-marker]:hidden">
                  <span>Что проверить лично</span>
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 14 14"
                    fill="none"
                    aria-hidden
                    className="shrink-0 text-slate-400 transition-transform group-open:rotate-180"
                  >
                    <path
                      d="M3.5 5.25 7 8.75l3.5-3.5"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </summary>
                <ul className="mt-2 list-disc space-y-1 pl-4 text-sm text-slate-600 dark:text-slate-300">
                  {checks.slice(0, 3).map((check) => (
                    <li key={check}>{check}</li>
                  ))}
                </ul>
              </details>

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
              <p className="mt-3 text-center text-[11px] leading-tight text-slate-400 dark:text-slate-500">
                Справочная расчётная оценка на основе открытых данных. Не является
                заключением экспертизы и не может использоваться для оценки стоимости
                недвижимости.
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
