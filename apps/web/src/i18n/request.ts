/**
 * next-intl request config: вызывается на сервере для каждого запроса
 * и возвращает messages для текущей locale.
 *
 * Locale читается из cookie ``NEXT_LOCALE`` (его выставляет middleware
 * в `apps/web/src/middleware.ts` на основе ``Accept-Language``).
 */

import { getRequestConfig } from "next-intl/server";
import { cookies } from "next/headers";

import { DEFAULT_LOCALE, LOCALE_COOKIE, asSupportedLocale } from "./config";

export default getRequestConfig(async () => {
  const cookieStore = await cookies();
  const locale = asSupportedLocale(cookieStore.get(LOCALE_COOKIE)?.value ?? DEFAULT_LOCALE);
  const messages = (await import(`../../messages/${locale}.json`)).default;
  return { locale, messages };
});
