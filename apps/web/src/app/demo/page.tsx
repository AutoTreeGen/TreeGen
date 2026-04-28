import type { Metadata } from "next";
import { useTranslations } from "next-intl";
import { getTranslations } from "next-intl/server";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * Phase 4.12 — public read-only demo tree.
 *
 * Hardcoded synthetic data (см. SAMPLE_TREE) — не задевает БД, не нужны
 * auth / consent. Owner перед launch'ем подменит на реальное демо-дерево
 * (вытянутое из staging БД через seed-скрипт), но контракт страницы
 * остаётся: «public read-only sample tree».
 */

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("demo");
  return {
    title: t("title"),
    description: t("subtitle"),
    openGraph: { title: t("title"), description: t("subtitle") },
  };
}

type SamplePerson = {
  id: string;
  name: string;
  birthYear: number | null;
  deathYear: number | null;
  generation: number;
};

type SampleMatch = {
  id: string;
  name: string;
  totalCm: number;
  predicted: string;
  segments: number;
};

const SAMPLE_TREE: SamplePerson[] = [
  { id: "p1", name: "Иван Петрович Иванов", birthYear: 1970, deathYear: null, generation: 0 },
  { id: "p2", name: "Пётр Иванович Иванов", birthYear: 1942, deathYear: 2018, generation: 1 },
  { id: "p3", name: "Анна Сергеевна Кузнецова", birthYear: 1945, deathYear: null, generation: 1 },
  { id: "p4", name: "Иван Алексеевич Иванов", birthYear: 1915, deathYear: 1990, generation: 2 },
  { id: "p5", name: "Мария Васильевна Соколова", birthYear: 1918, deathYear: 1995, generation: 2 },
  { id: "p6", name: "Сергей Николаевич Кузнецов", birthYear: 1920, deathYear: 1988, generation: 2 },
  { id: "p7", name: "Татьяна Ивановна Жукова", birthYear: 1922, deathYear: 1992, generation: 2 },
];

const SAMPLE_MATCH: SampleMatch = {
  id: "m1",
  name: "Olga K.",
  totalCm: 412,
  predicted: "1st cousin once removed",
  segments: 18,
};

export default function DemoPage() {
  const t = useTranslations("demo");
  return (
    <main className="mx-auto max-w-5xl px-6 py-16">
      <header className="mb-10">
        <Badge variant="outline">demo</Badge>
        <h1 className="mt-3 text-balance text-4xl font-bold tracking-tight">{t("title")}</h1>
        <p className="mt-3 max-w-2xl text-[color:var(--color-ink-500)]">{t("subtitle")}</p>
        <div className="mt-6">
          <Button variant="primary" size="md" asChild>
            <Link href="/sign-up">{t("cta")}</Link>
          </Button>
        </div>
      </header>

      <section aria-labelledby="demo-tree-heading" className="mb-12">
        <h2 id="demo-tree-heading" className="text-xl font-semibold">
          {t("treeHeader")}
        </h2>
        <div className="mt-4 grid gap-3">
          {[0, 1, 2].map((gen) => (
            <div key={gen} className="flex flex-wrap gap-3" data-generation={gen}>
              {SAMPLE_TREE.filter((p) => p.generation === gen).map((person) => (
                <Card key={person.id} className="min-w-[220px] flex-1">
                  <CardHeader>
                    <CardTitle className="text-base">{person.name}</CardTitle>
                    <CardDescription>
                      {person.birthYear ?? "?"}–{person.deathYear ?? "—"}
                    </CardDescription>
                  </CardHeader>
                </Card>
              ))}
            </div>
          ))}
        </div>
      </section>

      <section aria-labelledby="demo-match-heading" className="mb-12">
        <h2 id="demo-match-heading" className="text-xl font-semibold">
          {t("matchPlaceholder")}
        </h2>
        <Card className="mt-4 max-w-md">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {SAMPLE_MATCH.name}
              <Badge variant="outline">{SAMPLE_MATCH.predicted}</Badge>
            </CardTitle>
            <CardDescription>
              {SAMPLE_MATCH.totalCm.toFixed(1)} cM total · {SAMPLE_MATCH.segments} segments
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="link" size="sm" disabled>
              {t("viewMatch")}
            </Button>
          </CardContent>
        </Card>
      </section>

      <aside className="rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-surface-muted)] p-4 text-sm">
        <strong>{t("noteLabel")}:</strong> <span>{t("noteBody")}</span>
      </aside>
    </main>
  );
}
