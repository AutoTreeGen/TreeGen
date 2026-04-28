import type { Metadata, Viewport } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages } from "next-intl/server";
import type { ReactNode } from "react";

import { ClerkProvider } from "@clerk/nextjs";

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

/**
 * Phase 4.10 + 4.12: ClerkProvider оборачивает всё приложение (session
 * token доступен через ``useAuth().getToken()``), внутри —
 * NextIntlClientProvider для i18n. ``html lang`` берётся от next-intl
 * server-helper'а.
 */
export default async function RootLayout({ children }: { children: ReactNode }) {
  const locale = await getLocale();
  const messages = await getMessages();

  return (
    <ClerkProvider>
      <html lang={locale}>
        <body className="min-h-dvh antialiased">
          <NextIntlClientProvider locale={locale} messages={messages}>
            <Providers>
              {/* Phase 4.10 + 4.12 + 4.6: ClerkProvider → NextIntl → Providers →
                  ServiceWorkerBootstrap + OfflineIndicator (нужен QueryClient) +
                  SiteHeader (выше error-boundary для нав-resilience) +
                  GlobalErrorBoundary вокруг content-area. */}
              <ServiceWorkerBootstrap />
              <OfflineIndicator />
              <SiteHeader />
              <GlobalErrorBoundary>{children}</GlobalErrorBoundary>
            </Providers>
          </NextIntlClientProvider>
        </body>
      </html>
    </ClerkProvider>
  );
}
