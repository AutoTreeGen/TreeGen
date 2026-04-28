/**
 * Phase 4.12 — dashboard data placeholder.
 *
 * `getCurrentUserTreesCount()` сейчас всегда возвращает 0, потому что:
 *   - auth не подключён (Phase 4.10);
 *   - endpoint GET /users/me/trees не существует (Phase 4.13).
 *
 * Контракт функции: вернуть число деревьев текущего user'а. Когда обе
 * зависимости появятся, заменим тело на реальный fetch — сигнатуру и
 * вызывающий код (`apps/web/src/app/dashboard/page.tsx`) не трогаем.
 */

export async function getCurrentUserTreesCount(): Promise<number> {
  // TODO Phase 4.10/4.13: подменить на:
  //   const { userId } = await auth();
  //   const res = await fetch(`${API_BASE}/users/${userId}/trees`, ...);
  //   return res.json().total;
  return 0;
}
