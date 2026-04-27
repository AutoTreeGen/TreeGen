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

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      type="checkbox"
      className={cn(
        "h-4 w-4 cursor-pointer rounded border-[color:var(--color-border)]",
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
