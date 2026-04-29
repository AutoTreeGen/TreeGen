import { useTranslations } from "next-intl";
import Link from "next/link";
import { redirect } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getCurrentUserTreesCount } from "@/lib/dashboard-data";

/**
 * Phase 4.12 — dashboard skeleton with empty-state redirect.
 * Phase 4.13 — все строки переехали в `dashboard.*` namespace.
 *
 * Если у user'а 0 деревьев — редиректим на `/onboarding` (ADR-0035 §«Empty state»).
 *
 * `getCurrentUserTreesCount()` пока всегда возвращает 0 (нет auth'а
 * и endpoint'а GET /users/me/trees) — Phase 4.10/4.13b заменят на
 * реальный fetch без переписывания страницы.
 */
export default async function DashboardPage() {
  const treesCount = await getCurrentUserTreesCount();
  if (treesCount === 0) {
    redirect("/onboarding");
  }
  return <DashboardView treesCount={treesCount} />;
}

function DashboardView({ treesCount }: { treesCount: number }) {
  const t = useTranslations("dashboard");
  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">{t("title")}</h1>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">{t("subtitleStub")}</p>
      </header>
      <Card>
        <CardHeader>
          <CardTitle>{t("treeCount", { count: treesCount })}</CardTitle>
          <CardDescription>{t("treeCountDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="primary" size="md" asChild>
            <Link href="/onboarding">{t("ctaNewTree")}</Link>
          </Button>
        </CardContent>
      </Card>
    </main>
  );
}
