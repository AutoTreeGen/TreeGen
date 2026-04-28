import type { Metadata } from "next";
import type { ReactNode } from "react";

import { SiteHeader } from "@/components/site-header";

import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "AutoTreeGen — Tree view",
  description: "Read-only tree view for imported GEDCOM data (Phase 4.1).",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-dvh antialiased">
        <Providers>
          <SiteHeader />
          {children}
        </Providers>
      </body>
    </html>
  );
}
