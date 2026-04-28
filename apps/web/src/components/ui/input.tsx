import { type InputHTMLAttributes, forwardRef } from "react";

import { cn } from "@/lib/utils";

/**
 * Базовый Input — shadcn-style.
 * Стилистически совмещён с Button (та же высота h-10 на md, такая же
 * рамка через --color-border, focus ring через --color-accent).
 */
export type InputProps = InputHTMLAttributes<HTMLInputElement>;

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(
        "flex h-10 w-full rounded-md bg-[color:var(--color-surface)] px-3 py-2 text-sm",
        "ring-1 ring-[color:var(--color-border)] transition-colors",
        "placeholder:text-[color:var(--color-ink-500)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)]",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";
