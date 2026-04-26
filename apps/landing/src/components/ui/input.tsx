import { cn } from "@/lib/utils";
import { type InputHTMLAttributes, forwardRef } from "react";

export type InputProps = InputHTMLAttributes<HTMLInputElement>;

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, type = "text", ...props }, ref) => {
    return (
      <input
        ref={ref}
        type={type}
        className={cn(
          "flex h-12 w-full rounded-xl bg-[var(--color-surface)] px-4 text-base",
          "ring-1 ring-[var(--color-border-strong)] shadow-[var(--shadow-soft)]",
          "placeholder:text-[var(--color-ink-400)]",
          "focus-visible:outline-none focus-visible:ring-2",
          "focus-visible:ring-[var(--color-brand-500)] focus-visible:ring-offset-0",
          "disabled:cursor-not-allowed disabled:opacity-50",
          "transition-shadow duration-200",
          className,
        )}
        {...props}
      />
    );
  },
);
Input.displayName = "Input";
