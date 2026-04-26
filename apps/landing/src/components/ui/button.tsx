import { cn } from "@/lib/utils";
import { Slot } from "@radix-ui/react-slot";
import { type VariantProps, cva } from "class-variance-authority";
import { type ButtonHTMLAttributes, forwardRef } from "react";

/**
 * Базовый Button. Варианты: primary (фиолетовый CTA), secondary (outlined),
 * ghost (без фона), link.
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl font-medium " +
    "transition-all duration-200 ease-out focus-visible:outline-none focus-visible:ring-2 " +
    "focus-visible:ring-[var(--color-brand-500)] focus-visible:ring-offset-2 " +
    "disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0 " +
    "active:scale-[0.98]",
  {
    variants: {
      variant: {
        primary:
          "bg-[var(--color-brand-600)] text-white shadow-[var(--shadow-glow)] " +
          "hover:bg-[var(--color-brand-700)] hover:shadow-[var(--shadow-elevated)]",
        secondary:
          "bg-[var(--color-surface)] text-[var(--color-ink-900)] " +
          "ring-1 ring-[var(--color-border-strong)] shadow-[var(--shadow-soft)] " +
          "hover:bg-[var(--color-surface-muted)] hover:ring-[var(--color-brand-300)]",
        ghost:
          "text-[var(--color-ink-700)] hover:bg-[var(--color-brand-50)] " +
          "hover:text-[var(--color-brand-700)]",
        link:
          "text-[var(--color-brand-600)] underline-offset-4 hover:underline " +
          "hover:text-[var(--color-brand-700)]",
      },
      size: {
        sm: "h-9 px-3 text-sm",
        md: "h-11 px-5 text-sm",
        lg: "h-14 px-8 text-base",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "md",
    },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props} />
    );
  },
);
Button.displayName = "Button";
