import { redirect } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * Phase 4.12 — dashboard skeleton with empty-state redirect.
 *
 * Авторизованный лендинг для залогиненного пользователя. Если у user'а
 * 0 деревьев — редиректим на `/onboarding` (Phase 4.12 contract:
 * \"new user → wizard, never empty dashboard\").
 *
 * Phase 4.12 foundation: `getCurrentUserTreesCount()` — placeholder,
 * всегда возвращает 0 (нет ни auth'а, ни endpoint'а GET /users/me/trees).
 * Phase 4.10 / 4.13 заменят на реальный fetch — контракт страницы (если
 * 0 → редирект) при этом не меняется.
 */

import { getCurrentUserTreesCount } from "@/lib/dashboard-data";

export default async function DashboardPage() {
  const treesCount = await getCurrentUserTreesCount();
  if (treesCount === 0) {
    redirect("/onboarding");
  }

  // Phase 4.12 foundation: ниже — заглушка, потому что без auth'а сюда
  // мы вообще никогда не попадём с >0 деревьев. Контракт фиксирует
  // структуру страницы, чтобы Phase 4.13 заменил placeholder без
  // переписывания layout'а.
  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">
          Phase 4.12 placeholder. Real tree list lands in Phase 4.13.
        </p>
      </header>
      <Card>
        <CardHeader>
          <CardTitle>You have {treesCount} tree(s)</CardTitle>
          <CardDescription>
            Phase 4.13 will populate this with the real list of trees you own or collaborate on.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="primary" size="md" asChild>
            <a href="/onboarding">+ Start a new tree</a>
          </Button>
        </CardContent>
      </Card>
    </main>
  );
}
