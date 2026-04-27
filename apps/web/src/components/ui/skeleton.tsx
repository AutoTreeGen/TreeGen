import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Тонкий placeholder с pulse-анимацией для loading states.
 */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("animate-pulse rounded-md bg-[color:var(--color-surface-muted)]", className)}
      {...props}
    />
  );
}
