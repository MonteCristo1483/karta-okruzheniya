"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

// Одна страница «Поделиться» на весь сайт: /r/?b=<osm_id> (принимаем и ?id=).
// Статических страниц на дом больше нет: на Москве это ~100 тыс. файлов и
// ~700 МБ. Здесь нет ни данных, ни карточки — только чтение параметра и
// переход на карту, где дом открывается по ?building=<id> (map.tsx).
export default function SharePage() {
  const router = useRouter();
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const raw = (params.get("b") ?? params.get("id") ?? "").trim();
    if (/^\d+$/.test(raw)) {
      router.replace(`/?building=${raw}`);
      return;
    }
    setMissing(true);
  }, [router]);

  return (
    <main className="map-gradient flex min-h-dvh flex-col items-center justify-center gap-3 px-6 text-center">
      <div className="mb-1 flex items-center gap-2 text-sm font-bold text-slate-600 dark:text-slate-300">
        <span className="inline-block size-4 rounded-full border-[5px] border-[#6a9d76]" />
        Карта окружения
      </div>
      {missing ? (
        <>
          <h1 className="text-lg font-bold text-slate-800 dark:text-slate-100">
            Ссылка неполная
          </h1>
          <p className="max-w-xs text-sm text-slate-600 dark:text-slate-300">
            В ней нет номера дома. Найдите дом на карте — или откройте карту и
            вернитесь к ссылке.
          </p>
          <Link
            href="/"
            className="mt-2 rounded-xl bg-emerald-500 px-5 py-3 text-sm font-bold text-white transition-colors hover:bg-emerald-600"
          >
            Открыть карту
          </Link>
        </>
      ) : (
        <p className="text-sm text-slate-600 dark:text-slate-300" role="status">
          Открываем дом…
        </p>
      )}
    </main>
  );
}
