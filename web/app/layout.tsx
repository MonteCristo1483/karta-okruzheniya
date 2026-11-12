import type { Metadata, Viewport } from "next";
import Script from "next/script";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Карта окружения — Пресненский",
  manifest: "/karta-okruzheniya/manifest.json",
  icons: { icon: "/karta-okruzheniya/icon.svg" },
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
  },
};

export const viewport: Viewport = {
  themeColor: "#6a9d76",
};

const themeInit = `try{var t=localStorage.getItem("karta-theme");if(t)document.documentElement.dataset.theme=t}catch(e){}`;

// В dev (localhost) SW не регистрируем — кэширование чанков ломает HMR.
const swRegister = `if ("serviceWorker" in navigator && !["localhost","127.0.0.1"].includes(location.hostname)) { navigator.serviceWorker.register("/karta-okruzheniya/sw.js"); }`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru" suppressHydrationWarning>
      <body className="h-full">
        <Script
          id="theme-init"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: themeInit }}
        />
        <Script
          id="sw-register"
          strategy="afterInteractive"
          dangerouslySetInnerHTML={{ __html: swRegister }}
        />
        {children}
      </body>
    </html>
  );
}
