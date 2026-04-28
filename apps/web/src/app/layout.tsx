import type { Metadata, Viewport } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages } from "next-intl/server";
import type { ReactNode } from "react";

import { GlobalErrorBoundary } from "@/components/error-boundary";
import { OfflineIndicator } from "@/components/offline-indicator";
import { SiteHeader } from "@/components/site-header";
import { ServiceWorkerBootstrap } from "@/components/sw-bootstrap";

import { Providers } from "./providers";
import "./globals.css";

/**
 * Phase 4.12: SEO + i18n root.
 *
 * - `metadataBase` нужен Next 15 для абсолютных URL'ов в Open Graph.
 * - locale считывается на сервере (next-intl/server) и пробрасывается
 *   в `html lang` + `NextIntlClientProvider` для client-компонентов.
 */
export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://autotreegen.com"),
  title: {
    default: "AutoTreeGen — Evidence-based genealogy",
    template: "%s · AutoTreeGen",
  },
  description:
    "AI-powered genealogy platform. Bring GEDCOM, DNA matches, and archive sources into a single tree where every fact has provenance.",
  applicationName: "AutoTreeGen",
  authors: [{ name: "AutoTreeGen" }],
  keywords: [
    "genealogy",
    "GEDCOM",
    "DNA matching",
    "family tree",
    "evidence-based",
    "FamilySearch",
    "jewish genealogy",
  ],
  openGraph: {
    type: "website",
    siteName: "AutoTreeGen",
    title: "AutoTreeGen — Evidence-based genealogy",
    description:
      "AI-powered genealogy with provenance for every fact. GEDCOM + DNA + archives in one tree.",
  },
  twitter: {
    card: "summary_large_image",
    title: "AutoTreeGen — Evidence-based genealogy",
    description:
      "AI-powered genealogy with provenance for every fact. GEDCOM + DNA + archives in one tree.",
  },
};

export const viewport: Viewport = {
  themeColor: "#0f172a",
  width: "device-width",
  initialScale: 1,
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const locale = await getLocale();
  const messages = await getMessages();

  return (
    <html lang={locale}>
      <body className="min-h-dvh antialiased">
        <NextIntlClientProvider locale={locale} messages={messages}>
          <Providers>
            {/* OfflineIndicator + ServiceWorkerBootstrap — внутри Providers,
                чтобы ``useQueryClient`` нашёл provider. SiteHeader выше
                error-boundary, чтобы навигация работала даже при крэше
                content-area (см. ADR-0041 §«Per-route vs global»). */}
            <ServiceWorkerBootstrap />
            <OfflineIndicator />
            <SiteHeader />
            <GlobalErrorBoundary>{children}</GlobalErrorBoundary>
          </Providers>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
