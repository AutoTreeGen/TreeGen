import { type InputHTMLAttributes, forwardRef } from "react";

import { cn } from "@/lib/utils";

/**
 * Базовый Checkbox — нативный <input type="checkbox"> со shadcn-style
 * стилизацией. Не используем @radix-ui/react-checkbox чтобы не тащить
 * новую runtime-зависимость для одного MVP-toggle (Phase 4.4.1).
 *
 * Совмещён с Input/Button: same focus ring, --color-accent для checked
 * состояния через accent-color.
 */
export type CheckboxProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type">;

// Phase 4.14a — на mobile h-5 w-5 (20px hit-area + invisible padding via
// touch-target эмуляция) для большего пальце-комфорта; на ≥sm h-4 w-4.
// 16/20px недостаточно для WCAG 44px, но <input type=checkbox> сам по
// себе не поддерживает inflate; рекомендация: оборачивайте в <label>
// который и берёт touch-target — все наши вызовы так и делают.
export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      type="checkbox"
      className={cn(
        "h-5 w-5 cursor-pointer rounded border-[color:var(--color-border)] sm:h-4 sm:w-4",
        "accent-[color:var(--color-accent)]",
        "focus-visible:outline-none focus-visible:ring-2",
        "focus-visible:ring-[color:var(--color-accent)] focus-visible:ring-offset-2",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Checkbox.displayName = "Checkbox";
