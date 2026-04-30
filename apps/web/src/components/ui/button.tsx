import { Slot } from "@radix-ui/react-slot";
import { type VariantProps, cva } from "class-variance-authority";
import { type ButtonHTMLAttributes, forwardRef } from "react";

import { cn } from "@/lib/utils";

/**
 * Базовый Button — варианты primary / secondary / ghost / link.
 * Совместим с shadcn-style API (asChild, variant, size).
 *
 * Phase 4.14a — каждый размер получает `min-h-11` floor на mobile (<sm),
 * чтобы соблюсти WCAG 2.1 AA touch target ≥44×44px. На ≥sm возвращаемся
 * к компактным desktop-высотам (8/10/12) — UI с десятком mid-size кнопок
 * в фильтре не разъезжается. Visual height на ≥sm не меняется.
 *
 * `link` — отдельный случай: inline-текст внутри предложений. Для него
 * touch-target не делаем (был бы строкой 44px высоты внутри текста).
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium " +
    "transition-colors focus-visible:outline-none focus-visible:ring-2 " +
    "focus-visible:ring-[color:var(--color-accent)] focus-visible:ring-offset-2 " +
    "disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        primary: "bg-[color:var(--color-accent)] text-white hover:opacity-90",
        secondary:
          "bg-[color:var(--color-surface)] text-[color:var(--color-ink-900)] " +
          "ring-1 ring-[color:var(--color-border)] hover:bg-[color:var(--color-surface-muted)]",
        ghost: "text-[color:var(--color-ink-700)] hover:bg-[color:var(--color-surface-muted)]",
        link: "text-[color:var(--color-accent)] underline-offset-4 hover:underline",
        destructive: "bg-red-600 text-white hover:bg-red-700",
      },
      size: {
        sm: "min-h-11 px-3 text-sm sm:min-h-0 sm:h-8",
        md: "min-h-11 px-4 text-sm sm:min-h-0 sm:h-10",
        lg: "min-h-12 px-6 text-base sm:min-h-0 sm:h-12",
      },
    },
    compoundVariants: [
      // link не получает min-h-11 — он inline-элемент в тексте.
      { variant: "link", size: "sm", class: "min-h-0 sm:min-h-0" },
      { variant: "link", size: "md", class: "min-h-0 sm:min-h-0" },
      { variant: "link", size: "lg", class: "min-h-0 sm:min-h-0" },
    ],
    defaultVariants: {
      variant: "primary",
      size: "md",
    },
  },
);

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  };

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props} />
    );
  },
);
Button.displayName = "Button";
