"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

/**
 * Theme toggle для pilot-юзеров — переключает класс `.dark` на html-элементе
 * через next-themes. SSR-safe: до hydration рендерим placeholder той же
 * формы, чтобы избежать layout shift.
 *
 * Иконка: Moon в light-mode (предлагает "переключить в dark"), Sun в dark.
 */
export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // SSR-placeholder той же формы, чтобы header не прыгал на hydration
  if (!mounted) {
    return (
      <span
        aria-hidden="true"
        className="inline-flex h-10 w-10 items-center justify-center rounded-xl"
      />
    );
  }

  const isDark = resolvedTheme === "dark";

  return (
    <button
      type="button"
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className="inline-flex h-10 w-10 items-center justify-center rounded-xl
        bg-[var(--color-surface)] text-[var(--color-ink-700)]
        ring-1 ring-[var(--color-border-strong)] shadow-[var(--shadow-soft)]
        transition-all hover:text-[var(--color-brand-600)]
        hover:ring-[var(--color-brand-300)]
        focus-visible:outline-none focus-visible:ring-2
        focus-visible:ring-[var(--color-brand-500)] focus-visible:ring-offset-2"
    >
      {isDark ? (
        <Sun className="h-5 w-5" strokeWidth={2.2} />
      ) : (
        <Moon className="h-5 w-5" strokeWidth={2.2} />
      )}
    </button>
  );
}
