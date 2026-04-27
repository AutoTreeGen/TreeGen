import { useEffect, useState } from "react";

/**
 * Возвращает debounced версию value: обновляется не чаще раз в ``delayMs``.
 * Полезно для search-инпутов, чтобы не бить бэкенд на каждом keystroke.
 *
 * Использование:
 * ```tsx
 * const [q, setQ] = useState("");
 * const debouncedQ = useDebouncedValue(q, 300);
 * useQuery({ queryKey: [..., debouncedQ], queryFn: () => fetchSearch(debouncedQ) });
 * ```
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState<T>(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
