import { useTranslations } from "next-intl";
import Link from "next/link";

import { CheckMark } from "@/components/icons/check-mark";
import { LocaleSwitcher } from "@/components/locale-switcher";
import { Button } from "@/components/ui/button";
import { WaitlistForm } from "@/components/waitlist-form";

/**
 * Phase 4.12 — public landing.
 *
 * Server component: статика + i18n via next-intl. Все строки идут из
 * `messages/{en,ru}.json` namespace `landing` + `common`. Client-only
 * островки (waitlist form, locale switcher) — отдельными компонентами.
 *
 * Скриншоты pedigree-tree и chromosome painting — placeholder'ы (SVG
 * inline), реальные ассеты подменит owner перед публичным launch'ем.
 */
export default function LandingPage() {
  const tCommon = useTranslations("common");
  const tLanding = useTranslations("landing");

  return (
    <main>
      {/* Hero ---------------------------------------------------------- */}
      <section className="relative isolate overflow-hidden border-b border-[color:var(--color-border)]">
        <div className="mx-auto flex max-w-5xl flex-col items-center gap-8 px-6 py-24 text-center">
          <div className="flex w-full justify-end">
            <LocaleSwitcher />
          </div>
          <h1 className="text-balance text-4xl font-bold tracking-tight md:text-6xl">
            {tCommon("tagline")}
          </h1>
          <p className="max-w-3xl text-balance text-lg text-[color:var(--color-ink-500)] md:text-xl">
            {tCommon("subtagline")}
          </p>
          <div className="flex flex-wrap items-center justify-center gap-3">
            <Button variant="primary" size="lg" asChild>
              <Link href="/sign-up">{tCommon("ctaSignUp")}</Link>
            </Button>
            <Button variant="secondary" size="lg" asChild>
              <Link href="/demo">{tCommon("ctaDemo")}</Link>
            </Button>
          </div>
        </div>
      </section>

      {/* Value props --------------------------------------------------- */}
      <section
        aria-labelledby="value-props-heading"
        className="border-b border-[color:var(--color-border)] bg-[color:var(--color-surface-muted)]"
      >
        <div className="mx-auto max-w-6xl px-6 py-20">
          <h2 id="value-props-heading" className="sr-only">
            Why AutoTreeGen
          </h2>
          <div className="grid gap-8 md:grid-cols-2">
            <ValuePropCard
              title={tLanding("valueProps.evidence.title")}
              body={tLanding("valueProps.evidence.body")}
            />
            <ValuePropCard
              title={tLanding("valueProps.hypotheses.title")}
              body={tLanding("valueProps.hypotheses.body")}
            />
            <ValuePropCard
              title={tLanding("valueProps.unified.title")}
              body={tLanding("valueProps.unified.body")}
            />
            <ValuePropCard
              title={tLanding("valueProps.easternEurope.title")}
              body={tLanding("valueProps.easternEurope.body")}
            />
          </div>
        </div>
      </section>

      {/* Screenshots --------------------------------------------------- */}
      <section
        aria-label="Product screenshots"
        className="border-b border-[color:var(--color-border)]"
      >
        <div className="mx-auto grid max-w-6xl gap-10 px-6 py-20 md:grid-cols-2">
          <ScreenshotPlaceholder
            ariaLabel={tLanding("screenshots.pedigreeAlt")}
            caption={tLanding("screenshots.pedigreeCaption")}
            kind="pedigree"
          />
          <ScreenshotPlaceholder
            ariaLabel={tLanding("screenshots.chromosomeAlt")}
            caption={tLanding("screenshots.chromosomeCaption")}
            kind="chromosome"
          />
        </div>
      </section>

      {/* Pricing teaser ------------------------------------------------ */}
      <section
        aria-labelledby="pricing-heading"
        className="border-b border-[color:var(--color-border)] bg-[color:var(--color-surface-muted)]"
      >
        <div className="mx-auto max-w-5xl px-6 py-20">
          <h2 id="pricing-heading" className="text-center text-3xl font-bold tracking-tight">
            {tLanding("pricing.title")}
          </h2>
          <p className="mt-3 text-center text-[color:var(--color-ink-500)]">
            {tLanding("pricing.subtitle")}
          </p>
          <div className="mt-10 grid gap-6 md:grid-cols-2">
            <PricingTeaserCard
              name={tLanding("pricing.free.name")}
              price={tLanding("pricing.free.price")}
              tagline={tLanding("pricing.free.tagline")}
              features={[
                tLanding("pricing.free.feature1"),
                tLanding("pricing.free.feature2"),
                tLanding("pricing.free.feature3"),
              ]}
              ctaLabel={tLanding("pricing.free.cta")}
              ctaHref="/sign-up"
              variant="secondary"
            />
            <PricingTeaserCard
              name={tLanding("pricing.pro.name")}
              price={tLanding("pricing.pro.price")}
              tagline={tLanding("pricing.pro.tagline")}
              features={[
                tLanding("pricing.pro.feature1"),
                tLanding("pricing.pro.feature2"),
                tLanding("pricing.pro.feature3"),
                tLanding("pricing.pro.feature4"),
              ]}
              ctaLabel={tLanding("pricing.pro.cta")}
              ctaHref="/sign-up?plan=pro"
              variant="primary"
              highlighted
            />
          </div>
          <div className="mt-6 text-center">
            <Link
              href="/pricing"
              className="text-sm text-[color:var(--color-accent)] underline-offset-4 hover:underline"
            >
              {tLanding("pricing.viewFull")}
            </Link>
          </div>
        </div>
      </section>

      {/* Waitlist ------------------------------------------------------ */}
      <section
        aria-labelledby="waitlist-heading"
        className="border-b border-[color:var(--color-border)]"
      >
        <div className="mx-auto max-w-2xl px-6 py-20 text-center">
          <h2 id="waitlist-heading" className="text-3xl font-bold tracking-tight">
            {tLanding("waitlist.title")}
          </h2>
          <p className="mt-3 text-[color:var(--color-ink-500)]">{tLanding("waitlist.subtitle")}</p>
          <div className="mt-8">
            <WaitlistForm />
          </div>
        </div>
      </section>

      {/* Footer -------------------------------------------------------- */}
      <footer>
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 px-6 py-8 text-sm text-[color:var(--color-ink-500)] md:flex-row">
          <span>{tCommon("footerRights")}</span>
          <nav className="flex gap-4">
            <Link href="/privacy" className="hover:underline">
              {tCommon("footerPrivacy")}
            </Link>
            <Link href="/terms" className="hover:underline">
              {tCommon("footerTerms")}
            </Link>
            <Link href="/pricing" className="hover:underline">
              {tCommon("ctaPricing")}
            </Link>
          </nav>
        </div>
      </footer>
    </main>
  );
}

function ValuePropCard({ title, body }: { title: string; body: string }) {
  return (
    <article className="rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-6">
      <h3 className="text-xl font-semibold">{title}</h3>
      <p className="mt-3 text-[color:var(--color-ink-700)]">{body}</p>
    </article>
  );
}

function PricingTeaserCard({
  name,
  price,
  tagline,
  features,
  ctaLabel,
  ctaHref,
  variant,
  highlighted = false,
}: {
  name: string;
  price: string;
  tagline: string;
  features: string[];
  ctaLabel: string;
  ctaHref: string;
  variant: "primary" | "secondary";
  highlighted?: boolean;
}) {
  return (
    <div
      className={
        highlighted
          ? "rounded-lg border-2 border-[color:var(--color-accent)] bg-[color:var(--color-surface)] p-6 shadow-sm"
          : "rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface)] p-6"
      }
    >
      <header>
        <h3 className="text-2xl font-semibold">{name}</h3>
        <p className="mt-1 text-sm text-[color:var(--color-ink-500)]">{tagline}</p>
        <p className="mt-4 text-3xl font-bold">{price}</p>
      </header>
      <ul className="mt-6 space-y-2 text-sm">
        {features.map((f) => (
          <li key={f} className="flex items-start gap-2">
            <CheckMark className="mt-1 h-3.5 w-3.5 shrink-0 text-[color:var(--color-accent)]" />
            <span>{f}</span>
          </li>
        ))}
      </ul>
      <div className="mt-6">
        <Button variant={variant} size="md" className="w-full" asChild>
          <Link href={ctaHref}>{ctaLabel}</Link>
        </Button>
      </div>
    </div>
  );
}

/**
 * Inline SVG-плейсхолдеры для скриншотов. Owner подменит на реальные
 * растровые ассеты (`<Image>` с next/image) перед публичным запуском.
 * До тех пор — стилизованные mock'и, которые не нужно загружать
 * отдельным network round-trip'ом и не ломают LCP.
 */
function ScreenshotPlaceholder({
  ariaLabel,
  caption,
  kind,
}: {
  ariaLabel: string;
  caption: string;
  kind: "pedigree" | "chromosome";
}) {
  return (
    <figure>
      <div className="aspect-[16/10] overflow-hidden rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface)]">
        {kind === "pedigree" ? (
          <PedigreeMockSvg aria-label={ariaLabel} />
        ) : (
          <ChromosomeMockSvg aria-label={ariaLabel} />
        )}
      </div>
      <figcaption className="mt-3 text-sm text-[color:var(--color-ink-500)]">{caption}</figcaption>
    </figure>
  );
}

function PedigreeMockSvg(props: { "aria-label": string }) {
  return (
    <svg
      viewBox="0 0 320 200"
      role="img"
      aria-label={props["aria-label"]}
      xmlns="http://www.w3.org/2000/svg"
      className="h-full w-full"
    >
      <title>{props["aria-label"]}</title>
      <rect x="130" y="10" width="60" height="22" rx="4" fill="#10b981" />
      <text x="160" y="25" textAnchor="middle" fontSize="9" fill="white">
        person
      </text>
      <line x1="160" y1="32" x2="100" y2="60" stroke="#94a3b8" strokeWidth="1" />
      <line x1="160" y1="32" x2="220" y2="60" stroke="#94a3b8" strokeWidth="1" />
      <rect x="70" y="60" width="60" height="22" rx="4" fill="#0ea5e9" />
      <text x="100" y="75" textAnchor="middle" fontSize="9" fill="white">
        father
      </text>
      <rect x="190" y="60" width="60" height="22" rx="4" fill="#f59e0b" />
      <text x="220" y="75" textAnchor="middle" fontSize="9" fill="white">
        mother
      </text>
      {[
        [40, 110],
        [100, 110],
        [160, 110],
        [220, 110],
      ].map(([x, y]) => (
        <rect key={`g-${x}`} x={x} y={y} width="50" height="20" rx="3" fill="#cbd5e1" />
      ))}
      {[40, 100, 160, 220].map((x) => (
        <line key={`l-${x}`} x1={x + 25} y1={130} x2={x + 25} y2={150} stroke="#94a3b8" />
      ))}
      <line x1="100" y1="82" x2="40" y2="110" stroke="#94a3b8" />
      <line x1="100" y1="82" x2="100" y2="110" stroke="#94a3b8" />
      <line x1="220" y1="82" x2="160" y2="110" stroke="#94a3b8" />
      <line x1="220" y1="82" x2="220" y2="110" stroke="#94a3b8" />
      <text x="160" y="180" textAnchor="middle" fontSize="10" fill="#64748b">
        4 generations · 14 persons · 2 DNA-matched
      </text>
    </svg>
  );
}

function ChromosomeMockSvg(props: { "aria-label": string }) {
  return (
    <svg
      viewBox="0 0 320 200"
      role="img"
      aria-label={props["aria-label"]}
      xmlns="http://www.w3.org/2000/svg"
      className="h-full w-full"
    >
      <title>{props["aria-label"]}</title>
      {Array.from({ length: 10 }, (_, i) => {
        const y = 10 + i * 17;
        const length = 280 - i * 18;
        return (
          // Стабильный набор из 10 хромосом, без перестановок — idx безопасен.
          // biome-ignore lint/suspicious/noArrayIndexKey: stable order
          <g key={i}>
            <text
              x={20}
              y={y + 9}
              textAnchor="end"
              fontSize="9"
              fill="#374151"
              fontFamily="monospace"
            >
              {i + 1}
            </text>
            <rect x={26} y={y} width={length} height={11} rx={3} fill="#e5e7eb" stroke="#cbd5e1" />
            {i === 1 ? (
              <rect
                x={46}
                y={y + 1}
                width={70}
                height={9}
                rx={2}
                fill="#10b981"
                fillOpacity={0.85}
              />
            ) : null}
            {i === 5 ? (
              <rect
                x={120}
                y={y + 1}
                width={45}
                height={9}
                rx={2}
                fill="#10b981"
                fillOpacity={0.85}
              />
            ) : null}
            {i === 8 ? (
              <rect
                x={32}
                y={y + 1}
                width={32}
                height={9}
                rx={2}
                fill="#10b981"
                fillOpacity={0.85}
              />
            ) : null}
          </g>
        );
      })}
      <text x="160" y="195" textAnchor="middle" fontSize="10" fill="#64748b">
        Shared DNA — 3 segments · 56.4 cM total
      </text>
    </svg>
  );
}
