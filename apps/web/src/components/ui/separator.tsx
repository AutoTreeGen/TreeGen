import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Тонкий divider (горизонтальный или вертикальный) на brand-токенах границы.
 */
export function Separator({
  className,
  orientation = "horizontal",
  ...props
}: HTMLAttributes<HTMLDivElement> & { orientation?: "horizontal" | "vertical" }) {
  return (
    // Decorative divider: без роли, чтобы AT не воспринимали как navigable separator.
    <div
      aria-hidden="true"
      data-orientation={orientation}
      className={cn(
        "shrink-0 bg-[color:var(--color-border)]",
        orientation === "horizontal" ? "h-px w-full" : "h-full w-px",
        className,
      )}
      {...props}
    />
  );
}
