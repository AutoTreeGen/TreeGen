"use client";

import { useAuth } from "@clerk/nextjs";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { type ReactNode, useEffect, useState } from "react";

import { setAuthTokenProvider } from "@/lib/api";
import { setUserSettingsAuthTokenProvider } from "@/lib/user-settings-api";

/**
 * Корневой провайдер для client-side состояния.
 *
 * QueryClient создаётся через useState, чтобы один экземпляр жил весь session
 * (не пересоздавался на каждый ре-рендер). DevTools видны только в dev-сборке.
 *
 * Phase 4.10: внутри провайдера — :func:`ClerkAuthBridge` регистрирует
 * Clerk-token-getter в ``lib/api.ts``, чтобы все ``getJson``-вызовы
 * автоматически прикрепляли ``Authorization: Bearer <token>``.
 */
export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ClerkAuthBridge />
      {children}
      {process.env.NODE_ENV === "development" ? <ReactQueryDevtools initialIsOpen={false} /> : null}
    </QueryClientProvider>
  );
}

/**
 * Передать ``useAuth().getToken`` в ``lib/api.ts``-singleton.
 *
 * Без этого hook'а API-фабрики не знают, как достать JWT (они живут вне
 * Clerk-context'а, например в ``useQuery``-callback'ах). Один раз при
 * монтировании регистрируем функцию, которая на каждый запрос делает
 * `getToken()` (Clerk кэширует).
 *
 * Этот компонент ничего не рендерит — pure-side-effect.
 */
function ClerkAuthBridge(): null {
  const { getToken } = useAuth();
  useEffect(() => {
    const tokenGetter = async () => {
      const token = await getToken();
      return token ?? null;
    };
    setAuthTokenProvider(tokenGetter);
    setUserSettingsAuthTokenProvider(tokenGetter);
    return () => {
      setAuthTokenProvider(null);
      setUserSettingsAuthTokenProvider(null);
    };
  }, [getToken]);
  return null;
}
