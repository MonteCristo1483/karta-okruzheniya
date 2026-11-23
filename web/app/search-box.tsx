"use client";

import { useMemo, useState } from "react";

export type SearchItem = {
  id: number;
  address: string;
  index: number | null;
  year: number | null;
};

function normalize(value: string): string {
  return value
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/[^a-zа-я0-9]+/gi, " ")
    .trim();
}

export default function SearchBox({
  items,
  onSelect,
  onSearch,
}: {
  items: SearchItem[];
  onSelect: (id: number) => void;
  onSearch: () => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);

  const results = useMemo(() => {
    const q = normalize(query);
    if (q.length < 2) return [];
    const tokens = q.split(" ").filter(Boolean);
    const scored: { item: SearchItem; score: number }[] = [];
    for (const item of items) {
      const address = normalize(item.address);
      if (!tokens.every((token) => address.includes(token))) continue;
      let score = 0;
      if (address.startsWith(q)) score += 4;
      if (tokens.every((token) => address.split(" ").includes(token))) score += 2;
      if (item.index !== null) score += item.index / 100;
      scored.push({ item, score });
    }
    return scored
      .sort((a, b) => b.score - a.score || a.item.address.length - b.item.address.length)
      .slice(0, 8)
      .map((x) => x.item);
  }, [items, query]);

  const choose = (id: number) => {
    onSelect(id);
    setQuery("");
    setOpen(false);
  };

  return (
    <div className="absolute left-3 right-14 top-3 z-30 md:left-4 md:right-auto md:w-[clamp(260px,45vw,420px)] md:translate-x-0 xl:left-1/2 xl:w-[420px] xl:-translate-x-1/2">
      <div className="relative">
        <div className="flex items-center gap-2 rounded-2xl bg-white/95 px-3 py-2.5 shadow-lg backdrop-blur dark:bg-slate-900/95">
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            className="shrink-0 text-slate-400"
            aria-hidden
          >
            <circle cx="10.5" cy="10.5" r="6.5" stroke="currentColor" strokeWidth="2" />
            <path d="m15.5 15.5 4.5 4.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
          <input
            value={query}
            onChange={(e) => {
              onSearch();
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => {
              onSearch();
              setOpen(true);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && results[0]) {
                e.preventDefault();
                choose(results[0].id);
              } else if (e.key === "Escape") {
                setQuery("");
                setOpen(false);
              }
            }}
            placeholder="Адрес дома — улица, номер"
            aria-label="Поиск дома по адресу"
            autoComplete="off"
            inputMode="search"
            className="min-w-0 flex-1 bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400 dark:text-slate-100 dark:placeholder:text-slate-500"
          />
          {query && (
            <button
              type="button"
              onClick={() => {
                setQuery("");
                setOpen(false);
              }}
              aria-label="Очистить поиск"
              className="shrink-0 rounded-full p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
                <path d="M1 1l12 12M13 1L1 13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </button>
          )}
        </div>

        {open && query.trim().length >= 2 && (
          <div className="absolute left-0 right-0 top-full mt-1 max-h-72 overflow-y-auto rounded-2xl bg-white/95 p-1.5 shadow-xl backdrop-blur dark:bg-slate-900/95">
            {results.length === 0 ? (
              <div className="px-3 py-2 text-sm text-slate-500 dark:text-slate-400">
                Ничего не найдено. Попробуйте «Пресненская 12».
              </div>
            ) : (
              results.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => choose(item.id)}
                  className="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left hover:bg-slate-100 dark:hover:bg-slate-800"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                      {item.address}
                    </span>
                    {item.year !== null && (
                      <span className="block text-xs text-slate-400 dark:text-slate-500">
                        {item.year} год
                      </span>
                    )}
                  </span>
                  <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    {item.index === null ? "—" : item.index.toFixed(1)}
                  </span>
                </button>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
