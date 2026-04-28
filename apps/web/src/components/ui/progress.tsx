import { type HTMLAttributes, forwardRef } from "react";

import { cn } from "@/lib/utils";

/**
 * Тонкий progress-bar в shadcn-стилистике без зависимости на Radix.
 * Принимает ``value`` (0–100) и опциональный ``max``. Отрисовывает
 * заливку через CSS ``width: %`` с плавной transition.
 *
 * Когда ``value === null`` — рендерит indeterminate-полоску (бесконечная
 * анимация), полезно пока бэкенд ещё не прислал первый счётчик.
 *
 * Реализован как ``<div>`` поверх собственной разметки. Поскольку
 * ``role="progressbar"`` — статус-роль (не интерактивная), переносим
 * её на внутренний ``<span>`` без tabIndex: a11y-движок
 * биом-линтера применяет ``useFocusableInteractive`` только к корневому
 * элементу с интерактивной ролью.
 */
export type ProgressProps = HTMLAttributes<HTMLDivElement> & {
  value: number | null;
  max?: number;
  /** Лейбл для ассистивных технологий (см. WCAG 1.3.1). */
  ariaLabel?: string;
};

export const Progress = forwardRef<HTMLDivElement, ProgressProps>(
  ({ className, value, max = 100, ariaLabel, ...props }, ref) => {
    const isIndeterminate = value === null;
    const clamped = isIndeterminate ? 0 : Math.max(0, Math.min(value, max));
    const percent = isIndeterminate ? 0 : (clamped / max) * 100;

    return (
      <div
        ref={ref}
        className={cn(
          "relative h-2 w-full overflow-hidden rounded-full bg-[color:var(--color-surface-muted)]",
          className,
        )}
        {...props}
      >
        <span
          // tabIndex={-1} удовлетворяет biome lint/a11y/useFocusableInteractive,
          // не попадая при этом в tab-order: статус-роль не должна красть фокус.
          tabIndex={-1}
          role="progressbar"
          aria-label={ariaLabel}
          aria-valuemin={0}
          aria-valuemax={max}
          aria-valuenow={isIndeterminate ? undefined : clamped}
          aria-busy={isIndeterminate || percent < 100}
          className={cn(
            "block h-full bg-[color:var(--color-accent)] transition-[width] duration-300 ease-out",
            isIndeterminate && "w-1/3 animate-pulse",
          )}
          style={isIndeterminate ? undefined : { width: `${percent}%` }}
        />
      </div>
    );
  },
);
Progress.displayName = "Progress";
