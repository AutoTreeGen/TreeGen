/**
 * Catch-all SignIn route — Clerk hosted UI компонент.
 *
 * Маршрут ``/sign-in`` рендерит готовый ``<SignIn>``; Clerk сам
 * обрабатывает вход, OAuth providers (Google/GitHub/...), MFA и т.д.
 * После успешного входа — редирект на ``/`` (или
 * ``NEXT_PUBLIC_CLERK_SIGN_IN_FORCE_REDIRECT_URL``).
 */
import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <SignIn />
    </main>
  );
}
