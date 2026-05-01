"use client";

import { cn } from "@/lib/utils";
import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import { type ComponentPropsWithoutRef, type ElementRef, forwardRef } from "react";

// Inline tick SVG — lucide allowlist (ADR-0067 §«Enforcement» Decision A)
// не включает `Check`. SKILL.md §iconography разрешает «white interior strokes
// for chevrons / checks» — простой stroke-based path, не 3D-modern.
function CheckMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      className={className}
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3 8.5 L7 12 L13 4.5" />
    </svg>
  );
}

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
      <CheckMark className="h-3.5 w-3.5" />
    </CheckboxPrimitive.Indicator>
  </CheckboxPrimitive.Root>
));
Checkbox.displayName = CheckboxPrimitive.Root.displayName;
