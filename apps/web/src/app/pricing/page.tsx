import type { Metadata } from "next";
import { useTranslations } from "next-intl";
import { getTranslations } from "next-intl/server";
import Link from "next/link";

import { CheckMark } from "@/components/icons/check-mark";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * Phase 4.12 — full pricing page.
 *
 * Phase 4.12 содержит только Free и Pro tiers — Business / Enterprise
 * добавим в Phase 11 (биллинг). FAQ — простой, без accordion'а
 * (для SEO лучше развёрнутый текст в DOM).
 */

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("pricing");
  return {
    title: t("title"),
    description: t("subtitle"),
    openGraph: { title: t("title"), description: t("subtitle") },
  };
}

export default function PricingPage() {
  const t = useTranslations("pricing");
  const tLanding = useTranslations("landing.pricing");

  return (
    <main className="mx-auto max-w-5xl px-6 py-16">
      <header className="text-center">
        <h1 className="text-balance text-4xl font-bold tracking-tight">{t("title")}</h1>
        <p className="mt-3 text-[color:var(--color-ink-500)]">{t("subtitle")}</p>
      </header>

      <section aria-label="Plans" className="mt-12 grid gap-6 md:grid-cols-2">
        <PlanCard
          name={tLanding("free.name")}
          tagline={tLanding("free.tagline")}
          price={tLanding("free.price")}
          features={[
            tLanding("free.feature1"),
            tLanding("free.feature2"),
            tLanding("free.feature3"),
          ]}
          ctaLabel={tLanding("free.cta")}
          ctaHref="/sign-up"
          variant="secondary"
        />
        <PlanCard
          name={tLanding("pro.name")}
          tagline={tLanding("pro.tagline")}
          price={tLanding("pro.price")}
          features={[
            tLanding("pro.feature1"),
            tLanding("pro.feature2"),
            tLanding("pro.feature3"),
            tLanding("pro.feature4"),
          ]}
          ctaLabel={tLanding("pro.cta")}
          ctaHref="/sign-up?plan=pro"
          variant="primary"
          highlighted
        />
      </section>

      <section aria-labelledby="faq-heading" className="mt-16">
        <h2 id="faq-heading" className="text-2xl font-semibold">
          {t("faqTitle")}
        </h2>
        <dl className="mt-6 space-y-6">
          <FaqItem question={t("faq1Q")} answer={t("faq1A")} />
          <FaqItem question={t("faq2Q")} answer={t("faq2A")} />
          <FaqItem question={t("faq3Q")} answer={t("faq3A")} />
        </dl>
      </section>
    </main>
  );
}

function PlanCard({
  name,
  tagline,
  price,
  features,
  ctaLabel,
  ctaHref,
  variant,
  highlighted = false,
}: {
  name: string;
  tagline: string;
  price: string;
  features: string[];
  ctaLabel: string;
  ctaHref: string;
  variant: "primary" | "secondary";
  highlighted?: boolean;
}) {
  return (
    <Card
      className={highlighted ? "border-2 border-[color:var(--color-accent)] shadow-sm" : undefined}
    >
      <CardHeader>
        <CardTitle className="text-2xl">{name}</CardTitle>
        <CardDescription>{tagline}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <p className="text-3xl font-bold">{price}</p>
        <ul className="space-y-2 text-sm">
          {features.map((f) => (
            <li key={f} className="flex items-start gap-2">
              <CheckMark className="mt-1 h-3.5 w-3.5 shrink-0 text-[color:var(--color-accent)]" />
              <span>{f}</span>
            </li>
          ))}
        </ul>
        <Button variant={variant} size="md" className="w-full" asChild>
          <Link href={ctaHref}>{ctaLabel}</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

function FaqItem({ question, answer }: { question: string; answer: string }) {
  return (
    <div>
      <dt className="font-semibold">{question}</dt>
      <dd className="mt-1 text-[color:var(--color-ink-500)]">{answer}</dd>
    </div>
  );
}
