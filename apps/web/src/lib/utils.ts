import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * `cn` — мержит Tailwind-классы с разрешением конфликтов (twMerge) и
 * условной композицией (clsx). Стандарт shadcn/ui.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
