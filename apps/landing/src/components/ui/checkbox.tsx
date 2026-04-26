"use client";

import { cn } from "@/lib/utils";
import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import { Check } from "lucide-react";
import { type ComponentPropsWithoutRef, type ElementRef, forwardRef } from "react";

/** Кастомный checkbox с фиолетовой галочкой. Использует Radix для a11y. */
export const Checkbox = forwardRef<
  ElementRef<typeof CheckboxPrimitive.Root>,
  ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root>
>(({ className, ...props }, ref) => (
  <CheckboxPrimitive.Root
    ref={ref}
    className={cn(
      "peer h-5 w-5 shrink-0 rounded-md border-2 border-[var(--color-border-strong)]",
      "bg-[var(--color-surface)] transition-colors",
      "focus-visible:outline-none focus-visible:ring-2",
      "focus-visible:ring-[var(--color-brand-500)] focus-visible:ring-offset-2",
      "disabled:cursor-not-allowed disabled:opacity-50",
      "data-[state=checked]:bg-[var(--color-brand-600)]",
      "data-[state=checked]:border-[var(--color-brand-600)]",
      "data-[state=checked]:text-white",
      "hover:border-[var(--color-brand-400)]",
      className,
    )}
    {...props}
  >
    <CheckboxPrimitive.Indicator className="flex items-center justify-center text-current">
      <Check className="h-3.5 w-3.5" strokeWidth={3} />
    </CheckboxPrimitive.Indicator>
  </CheckboxPrimitive.Root>
));
Checkbox.displayName = CheckboxPrimitive.Root.displayName;
