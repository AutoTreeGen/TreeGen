/**
 * Catch-all SignUp route — Clerk hosted UI компонент.
 *
 * Аналог ``/sign-in``. ``<SignUp>`` обрабатывает регистрацию +
 * email-verification + welcome-flow.
 */
import { SignUp } from "@clerk/nextjs";

export default function SignUpPage() {
  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <SignUp />
    </main>
  );
}
