/**
 * Vitest для /trees/[id]/access и /invite/[token] (Phase 11.1).
 *
 * Покрывает minimum-must:
 *   - access-page рендерит membership list с masked email и Owner badge;
 *   - invite-form валидация email;
 *   - invite/[token] page вызывает acceptInvitation на mount и редиректит;
 *   - invite/[token] page показывает error при 410 (expired).
 *
 * Сетевые вызовы api.ts мокаются через vi.hoisted; реальный fetch не идёт.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockReplace = vi.fn();
const mockRouter = { push: vi.fn(), replace: mockReplace, back: vi.fn() };
const mockParams: { id?: string; token?: string } = {};

vi.mock("next/navigation", () => ({
  useParams: () => mockParams,
  useRouter: () => mockRouter,
  useSearchParams: () => new URLSearchParams(),
}));

const apiMocks = vi.hoisted(() => ({
  fetchMembers: vi.fn(),
  fetchInvitations: vi.fn(),
  createInvitation: vi.fn(),
  revokeMember: vi.fn(),
  updateMemberRole: vi.fn(),
  resendInvitation: vi.fn(),
  revokeInvitation: vi.fn(),
  transferOwnership: vi.fn(),
  acceptInvitation: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    ...apiMocks,
  };
});

import InviteAcceptPage from "@/app/invite/[token]/page";
import AccessPage from "@/app/trees/[id]/access/page";
import { ApiError } from "@/lib/api";

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  for (const m of Object.values(apiMocks)) m.mockReset();
  mockReplace.mockReset();
  mockParams.id = undefined;
  mockParams.token = undefined;
});

// ---------------------------------------------------------------------------
// /trees/[id]/access
// ---------------------------------------------------------------------------

describe("AccessPage", () => {
  it("renders members with masked email and Owner badge", async () => {
    mockParams.id = "tree-123";
    apiMocks.fetchMembers.mockResolvedValue({
      tree_id: "tree-123",
      items: [
        {
          id: "m1",
          user_id: "u1",
          email: "owner@example.com",
          display_name: "Tree Owner",
          role: "owner",
          invited_by: null,
          joined_at: "2026-04-28T00:00:00Z",
          revoked_at: null,
        },
        {
          id: "m2",
          user_id: "u2",
          email: "cousin@example.com",
          display_name: null,
          role: "viewer",
          invited_by: "u1",
          joined_at: "2026-04-28T00:00:00Z",
          revoked_at: null,
        },
      ],
    });
    apiMocks.fetchInvitations.mockResolvedValue({ tree_id: "tree-123", items: [] });

    renderWithClient(<AccessPage />);

    expect(await screen.findByText("Tree Owner")).toBeInTheDocument();
    // "Owner" появляется как badge единожды (radio-options только Editor/Viewer).
    expect(screen.getByText("Owner")).toBeInTheDocument();
    // Masked email of cousin: c****n@example.com (4 stars, length-2 in middle).
    expect(screen.getByText(/c\*+n@example\.com/)).toBeInTheDocument();
  });

  it("rejects invalid email in invite form", async () => {
    mockParams.id = "tree-123";
    apiMocks.fetchMembers.mockResolvedValue({ tree_id: "tree-123", items: [] });
    apiMocks.fetchInvitations.mockResolvedValue({ tree_id: "tree-123", items: [] });

    renderWithClient(<AccessPage />);
    const user = userEvent.setup();

    const input = await screen.findByLabelText(/Invitee email/i);
    await user.type(input, "not-an-email");
    await user.click(screen.getByRole("button", { name: /Send invitation/i }));

    expect(await screen.findByText(/valid email/i)).toBeInTheDocument();
    expect(apiMocks.createInvitation).not.toHaveBeenCalled();
  });

  it("posts invitation on valid submit", async () => {
    mockParams.id = "tree-123";
    apiMocks.fetchMembers.mockResolvedValue({ tree_id: "tree-123", items: [] });
    apiMocks.fetchInvitations.mockResolvedValue({ tree_id: "tree-123", items: [] });
    apiMocks.createInvitation.mockResolvedValue({
      id: "inv-1",
      tree_id: "tree-123",
      invitee_email: "guest@example.com",
      role: "viewer",
      token: "tok",
      invite_url: "http://localhost/invite/tok",
      expires_at: "2026-05-12T00:00:00Z",
      accepted_at: null,
      revoked_at: null,
      created_at: "2026-04-28T00:00:00Z",
    });

    renderWithClient(<AccessPage />);
    const user = userEvent.setup();
    const input = await screen.findByLabelText(/Invitee email/i);
    await user.type(input, "guest@example.com");
    await user.click(screen.getByRole("button", { name: /Send invitation/i }));

    await waitFor(() =>
      expect(apiMocks.createInvitation).toHaveBeenCalledWith(
        "tree-123",
        "guest@example.com",
        "viewer",
      ),
    );
  });
});

// ---------------------------------------------------------------------------
// /invite/[token]
// ---------------------------------------------------------------------------

describe("InviteAcceptPage", () => {
  it("calls acceptInvitation on mount and redirects on success", async () => {
    mockParams.token = "tok-abc";
    apiMocks.acceptInvitation.mockResolvedValue({
      tree_id: "tree-xyz",
      membership_id: "m1",
      role: "viewer",
    });

    renderWithClient(<InviteAcceptPage />);

    await waitFor(() => expect(apiMocks.acceptInvitation).toHaveBeenCalledWith("tok-abc"));
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/trees/tree-xyz/persons"));
  });

  it("shows expired/revoked message on 410", async () => {
    mockParams.token = "tok-expired";
    apiMocks.acceptInvitation.mockRejectedValue(new ApiError(410, "Invitation has expired"));

    renderWithClient(<InviteAcceptPage />);

    expect(await screen.findByText(/expired or was revoked/i)).toBeInTheDocument();
    expect(mockReplace).not.toHaveBeenCalledWith(expect.stringMatching(/^\/trees\//));
  });

  it("redirects to /sign-in on 401", async () => {
    mockParams.token = "tok-unauth";
    apiMocks.acceptInvitation.mockRejectedValue(new ApiError(401, "Not signed in"));

    renderWithClient(<InviteAcceptPage />);

    await waitFor(() =>
      expect(mockReplace).toHaveBeenCalledWith("/sign-in?redirect=/invite/tok-unauth"),
    );
  });
});
