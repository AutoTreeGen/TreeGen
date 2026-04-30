import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "@/lib/api";
import enMessages from "../../../../messages/en.json";

/**
 * Phase 11.1 — vitest для accept-flow `/invitations/[token]`.
 *
 * 4 state'а из task spec'а покрываем явно через mock'и Clerk + API:
 *   1. Token invalid/expired (lookup.kind === "invalid").
 *   2. Token valid, user not signed in (SignedOut branch).
 *   3. Token valid, signed in, email mismatch.
 *   4. Token valid, signed in, email match → Accept button.
 *
 * Мокаем @clerk/nextjs целиком: SignedIn/SignedOut вычисляются из flag'а;
 * useUser возвращает фиктивного user'а с заданным email.
 */

let mockSignedIn = false;
let mockUserEmail: string | null = null;

vi.mock("@clerk/nextjs", () => ({
  SignedIn: ({ children }: { children: ReactNode }) => (mockSignedIn ? <>{children}</> : null),
  SignedOut: ({ children }: { children: ReactNode }) => (!mockSignedIn ? <>{children}</> : null),
  SignInButton: ({ children }: { children: ReactNode }) => <>{children}</>,
  SignUpButton: ({ children }: { children: ReactNode }) => <>{children}</>,
  useUser: () => ({
    user: mockUserEmail ? { primaryEmailAddress: { emailAddress: mockUserEmail } } : null,
    isLoaded: true,
  }),
}));

const mockReplace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace, push: vi.fn() }),
  useParams: () => ({ token: "tok-test" }),
}));

import {
  InvitationAcceptClient,
  type LookupResult,
} from "@/app/invitations/[token]/invitation-accept-client";

function wrap(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return (
    <NextIntlClientProvider locale="en" messages={enMessages}>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </NextIntlClientProvider>
  );
}

const VALID_LOOKUP: LookupResult = {
  kind: "ok",
  data: {
    invitee_email: "invitee@example.com",
    role: "editor",
    tree_id: "tree-abc",
    tree_name: "Smith Family Tree",
    inviter_display_name: "Aunt May",
    expires_at: "2026-12-31T00:00:00Z",
    accepted_at: null,
  },
};

afterEach(() => {
  mockSignedIn = false;
  mockUserEmail = null;
  mockReplace.mockReset();
  vi.restoreAllMocks();
});

describe("InvitationAcceptClient", () => {
  it("state 1 — expired/revoked token shows invalid card with go-to-dashboard", () => {
    render(wrap(<InvitationAcceptClient token="tok-test" lookup={{ kind: "invalid" }} />));

    expect(screen.getByText("Invitation is no longer valid")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to dashboard" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
  });

  it("state 1b — not_found token shows not-found card", () => {
    render(wrap(<InvitationAcceptClient token="tok-test" lookup={{ kind: "not_found" }} />));

    expect(screen.getByText("Invitation not found")).toBeInTheDocument();
  });

  it("state 2 — valid lookup, signed-out shows sign-in CTA", () => {
    mockSignedIn = false;
    render(wrap(<InvitationAcceptClient token="tok-test" lookup={VALID_LOOKUP} />));

    expect(screen.getByText("Smith Family Tree", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create account" })).toBeInTheDocument();
    // Accept button must NOT be present in signed-out state.
    expect(screen.queryByRole("button", { name: "Accept invitation" })).toBeNull();
  });

  it("state 3 — signed in with mismatched email shows warning", () => {
    mockSignedIn = true;
    mockUserEmail = "wrong@example.com";

    render(wrap(<InvitationAcceptClient token="tok-test" lookup={VALID_LOOKUP} />));

    expect(screen.getByText("This invitation is for a different email")).toBeInTheDocument();
    expect(screen.getByText(/wrong@example.com/)).toBeInTheDocument();
    // invitee email mentioned in mismatch body + sign-in CTA link.
    expect(screen.getAllByText(/invitee@example.com/).length).toBeGreaterThanOrEqual(1);
    // Accept button hidden on email mismatch.
    expect(screen.queryByRole("button", { name: "Accept invitation" })).toBeNull();
  });

  it("state 4 — signed in with matching email shows Accept button and triggers POST", async () => {
    mockSignedIn = true;
    mockUserEmail = "invitee@example.com";

    const accept = vi.spyOn(api, "acceptInvitation").mockResolvedValue({
      tree_id: "tree-abc",
      membership_id: "mem-1",
      role: "editor",
    });

    render(wrap(<InvitationAcceptClient token="tok-test" lookup={VALID_LOOKUP} />));

    const acceptBtn = screen.getByRole("button", { name: "Accept invitation" });
    await userEvent.click(acceptBtn);

    await waitFor(() => {
      expect(accept).toHaveBeenCalledWith("tok-test");
    });
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/trees/tree-abc");
    });
  });

  it("already-accepted invitation shows Open tree link instead of Accept", () => {
    mockSignedIn = true;
    mockUserEmail = "invitee@example.com";

    const lookup: LookupResult = {
      kind: "ok",
      data: {
        ...VALID_LOOKUP.data,
        accepted_at: "2026-04-01T00:00:00Z",
      },
    };
    render(wrap(<InvitationAcceptClient token="tok-test" lookup={lookup} />));

    expect(screen.getByText("You've already accepted this invitation")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open tree" })).toHaveAttribute(
      "href",
      "/trees/tree-abc",
    );
  });
});
