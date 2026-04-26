import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata, Viewport } from "next";
import "./globals.css";

/**
 * Корневой layout. SEO + базовые fonts.
 * Geist подключается через `geist` пакет — самый чистый способ для Next.js.
 * Иконки и социальные превью — из brand v1.0 ассетов (см. assets/brand/).
 */
export const viewport: Viewport = {
  themeColor: "#4B2D8C",
};

export const metadata: Metadata = {
  metadataBase: new URL("https://autotreegen.com"),
  title: {
    default: "AutoTreeGen — From DNA to truth",
    template: "%s · AutoTreeGen",
  },
  description:
    "Evidence-based scientific genealogy. Unify GEDCOM, DNA, and archive sources into a verified family tree powered by AI hypothesis engine.",
  keywords: [
    "genealogy",
    "GEDCOM",
    "DNA analysis",
    "family tree",
    "evidence-based",
    "AI genealogy",
    "ancestry research",
  ],
  authors: [{ name: "AutoTreeGen" }],
  openGraph: {
    title: "AutoTreeGen — From DNA to truth",
    description:
      "Evidence-based scientific genealogy. Unify GEDCOM, DNA, and archive sources into a verified family tree.",
    url: "https://autotreegen.com",
    siteName: "AutoTreeGen",
    locale: "en_US",
    type: "website",
    images: [
      {
        url: "/og-image.png",
        width: 1200,
        height: 630,
        alt: "AutoTreeGen — From DNA to truth",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "AutoTreeGen — From DNA to truth",
    description: "Evidence-based scientific genealogy.",
    images: ["/og-image.png"],
  },
  robots: {
    index: true,
    follow: true,
  },
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/favicon.ico", sizes: "any" },
      { url: "/icon-192.png", type: "image/png", sizes: "192x192" },
      { url: "/icon-512.png", type: "image/png", sizes: "512x512" },
    ],
    apple: "/apple-touch-icon.png",
  },
  manifest: "/manifest.webmanifest",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="bg-canvas text-ink-900 antialiased">{children}</body>
    </html>
  );
}
