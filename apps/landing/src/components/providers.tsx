"use client";

import { ThemeProvider } from "next-themes";
import type { ReactNode } from "react";

/**
 * Client-side provider barrel. Сейчас — только next-themes, дальше сюда
 * пойдут TanStack Query / analytics / другие client-side контексты.
 *
 * `attribute="class"` сетит "dark"/"light" на <html> — комплементарно
 * `@variant dark (.dark *)` в globals.css.
 *
 * `defaultTheme="system"` + `enableSystem` — уважаем prefers-color-scheme,
 * пока пользователь не нажал toggle.
 *
 * `disableTransitionOnChange` — убирает мигание transition'ов в момент
 * смены темы (когда CSS-переменные дёрнуло).
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      {children}
    </ThemeProvider>
  );
}
