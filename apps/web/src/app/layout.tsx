import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ClerkProvider } from "@clerk/nextjs";

import { SiteHeader } from "@/components/site-header";

import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "AutoTreeGen — Tree view",
  description: "Read-only tree view for imported GEDCOM data (Phase 4.1).",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  // Phase 4.10: ClerkProvider оборачивает приложение, делает session-токен
  // доступным через ``useAuth().getToken()`` в client-компонентах. SignIn /
  // SignUp страницы — на /sign-in и /sign-up, см. middleware.ts.
  return (
    <ClerkProvider>
      <html lang="en">
        <body className="min-h-dvh antialiased">
          <Providers>
            <SiteHeader />
            {children}
          </Providers>
        </body>
      </html>
    </ClerkProvider>
  );
}
