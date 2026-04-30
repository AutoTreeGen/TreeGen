/**
 * Phase 11.1 — клиентский провайдер списка деревьев текущего user'а
 * для tree-picker dropdown в ``<SiteHeader>``.
 *
 * Контракт: возвращает active+shared деревья, отсортированные с
 * last-active первым. Текущий выбор хранится в cookie `current_tree_id`
 * — на стороне сервера middleware читает её при /trees/ редиректах,
 * на стороне клиента tree-picker подсвечивает совпадение.
 *
 * Backend endpoint `GET /users/me/trees` пока не существует (его
 * добавит Phase 4.13c после auth-полного flow). До тех пор возвращаем
 * пустой список — dropdown скрывает сам себя при 0 деревьев (см. task
 * spec §3 «Если 0 trees — не рендерить dropdown»).
 *
 * Когда endpoint появится, заменим тело fetchUserTrees() — сигнатуру
 * и call-site-ы (site-header) трогать не придётся.
 */

export type UserTreeSummary = {
  /** UUID дерева. */
  id: string;
  name: string;
  /** Роль в этом дереве: owner / editor / viewer. */
  role: "owner" | "editor" | "viewer";
  /** ISO timestamp последней активности — используется для сортировки. */
  last_active_at: string | null;
};

export type UserTreesResponse = {
  items: UserTreeSummary[];
};

/**
 * Клиентский fetcher для tree-picker. Сейчас stub: возвращает [].
 * После появления `GET /users/me/trees` заменить на:
 *
 *     return getJson<UserTreesResponse>("/users/me/trees");
 */
export async function fetchUserTrees(): Promise<UserTreesResponse> {
  return { items: [] };
}

const CURRENT_TREE_COOKIE = "current_tree_id";

export function readCurrentTreeId(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.split("; ").find((c) => c.startsWith(`${CURRENT_TREE_COOKIE}=`));
  if (!match) return null;
  const [, value] = match.split("=");
  return value ? decodeURIComponent(value) : null;
}

export function writeCurrentTreeId(treeId: string): void {
  if (typeof document === "undefined") return;
  // 1 год, path=/ чтобы попадало во все маршруты, SameSite=Lax — ок для этого
  // навигационного state'а (нет cross-site sensitive операций).
  const maxAge = 60 * 60 * 24 * 365;
  document.cookie = `${CURRENT_TREE_COOKIE}=${encodeURIComponent(
    treeId,
  )}; path=/; max-age=${maxAge}; samesite=lax`;
}
