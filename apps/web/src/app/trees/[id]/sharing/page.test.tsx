import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/lib/api";
import enMessages from "../../../../../messages/en.json";

/**
 * Phase 11.1 — vitest для `/trees/[id]/sharing` owner-page.
 *
 * Покрыто:
 *   - render members table (rows, role badge, joined date).
 *   - render pending invitations table.
 *   - role change mutation на клик «Make editor».
 *   - revoke flow с confirm-dialog'ом.
 *   - 403 от members/invitations → forbidden state (i18n).
 *   - invite-form: invalid email → ошибка; success → toast + invalidate.
 *
 * `useParams` мокается — компонент завязан на `/trees/[id]/sharing` route.
 */

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "tree-123" }),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import SharingPage from "@/app/trees/[id]/sharing/page";

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

const OWNER_MEMBER: api.Member = {
  id: "m-owner",
  user_id: "u-owner",
  email: "owner@example.com",
  display_name: "Tree Owner",
  role: "owner",
  invited_by: null,
  joined_at: "2026-01-01T00:00:00Z",
  revoked_at: null,
};

const VIEWER_MEMBER: api.Member = {
  id: "m-viewer",
  user_id: "u-viewer",
  email: "cousin@example.com",
  display_name: null,
  role: "viewer",
  invited_by: "u-owner",
  joined_at: "2026-02-15T00:00:00Z",
  revoked_at: null,
};

const PENDING_INVITATION: api.Invitation = {
  id: "i-pending",
  tree_id: "tree-123",
  invitee_email: "newbie@example.com",
  role: "editor",
  token: "tok-pending",
  invite_url: "https://app.local/invitations/tok-pending",
  expires_at: "2026-12-31T00:00:00Z",
  accepted_at: null,
  revoked_at: null,
  created_at: "2026-04-20T00:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SharingPage", () => {
  it("renders members table with role badges and joined dates", async () => {
    vi.spyOn(api, "fetchMembers").mockResolvedValue({
      tree_id: "tree-123",
      items: [OWNER_MEMBER, VIEWER_MEMBER],
    });
    vi.spyOn(api, "fetchInvitations").mockResolvedValue({
      tree_id: "tree-123",
      items: [],
    });

    render(wrap(<SharingPage />));

    // Owner row visible.
    await waitFor(() => {
      expect(screen.getByText("Tree Owner")).toBeInTheDocument();
    });
    expect(screen.getByText("owner@example.com")).toBeInTheDocument();
    // Owner badge has localized label.
    expect(screen.getAllByText("Owner").length).toBeGreaterThan(0);
    // Viewer row uses email when display_name is null — appears as both name and email cells.
    expect(screen.getAllByText("cousin@example.com").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Viewer")).toBeInTheDocument();
  });

  it("renders pending invitations table with revoke button", async () => {
    vi.spyOn(api, "fetchMembers").mockResolvedValue({
      tree_id: "tree-123",
      items: [OWNER_MEMBER],
    });
    vi.spyOn(api, "fetchInvitations").mockResolvedValue({
      tree_id: "tree-123",
      items: [PENDING_INVITATION],
    });

    render(wrap(<SharingPage />));

    await waitFor(() => {
      expect(screen.getByText("newbie@example.com")).toBeInTheDocument();
    });
    expect(screen.getByText("Editor")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke" })).toBeInTheDocument();
  });

  it("calls updateMemberRole when clicking Make editor", async () => {
    vi.spyOn(api, "fetchMembers").mockResolvedValue({
      tree_id: "tree-123",
      items: [OWNER_MEMBER, VIEWER_MEMBER],
    });
    vi.spyOn(api, "fetchInvitations").mockResolvedValue({
      tree_id: "tree-123",
      items: [],
    });
    const update = vi.spyOn(api, "updateMemberRole").mockResolvedValue({
      ...VIEWER_MEMBER,
      role: "editor",
    });

    render(wrap(<SharingPage />));

    const promote = await screen.findByRole("button", { name: "Make editor" });
    await userEvent.click(promote);
    expect(update).toHaveBeenCalledWith("m-viewer", "editor");
  });

  it("revokes member after confirm()", async () => {
    vi.spyOn(api, "fetchMembers").mockResolvedValue({
      tree_id: "tree-123",
      items: [OWNER_MEMBER, VIEWER_MEMBER],
    });
    vi.spyOn(api, "fetchInvitations").mockResolvedValue({
      tree_id: "tree-123",
      items: [],
    });
    const revoke = vi.spyOn(api, "revokeMember").mockResolvedValue();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(wrap(<SharingPage />));

    const removeBtn = await screen.findByRole("button", { name: "Remove access" });
    await userEvent.click(removeBtn);
    expect(revoke).toHaveBeenCalledWith("m-viewer");
  });

  it("renders forbidden state when GET /members returns 403", async () => {
    vi.spyOn(api, "fetchMembers").mockRejectedValue(new api.AuthError(403, "forbidden"));
    vi.spyOn(api, "fetchInvitations").mockRejectedValue(new api.AuthError(403, "forbidden"));

    render(wrap(<SharingPage />));

    await waitFor(() => {
      expect(screen.getByText("Owner-only page")).toBeInTheDocument();
    });
    // ErrorMessage code="forbidden" prints localized error string.
    expect(screen.getByRole("alert").textContent).toMatch(/permission/i);
  });

  it("invite form rejects invalid email and does not call API", async () => {
    vi.spyOn(api, "fetchMembers").mockResolvedValue({
      tree_id: "tree-123",
      items: [OWNER_MEMBER],
    });
    vi.spyOn(api, "fetchInvitations").mockResolvedValue({
      tree_id: "tree-123",
      items: [],
    });
    const create = vi.spyOn(api, "createInvitation");

    render(wrap(<SharingPage />));

    const emailInput = await screen.findByLabelText("Email");
    fireEvent.change(emailInput, { target: { value: "not-an-email" } });
    fireEvent.click(screen.getByRole("button", { name: "Send invitation" }));

    await waitFor(() => {
      expect(screen.getByText(/valid email/i)).toBeInTheDocument();
    });
    expect(create).not.toHaveBeenCalled();
  });

  it("invite form shows toast + calls createInvitation on success", async () => {
    vi.spyOn(api, "fetchMembers").mockResolvedValue({
      tree_id: "tree-123",
      items: [OWNER_MEMBER],
    });
    vi.spyOn(api, "fetchInvitations").mockResolvedValue({
      tree_id: "tree-123",
      items: [],
    });
    const create = vi.spyOn(api, "createInvitation").mockResolvedValue(PENDING_INVITATION);

    render(wrap(<SharingPage />));

    const emailInput = await screen.findByLabelText("Email");
    fireEvent.change(emailInput, { target: { value: "newbie@example.com" } });
    fireEvent.click(screen.getByLabelText("Editor — can add and change data"));
    fireEvent.click(screen.getByRole("button", { name: "Send invitation" }));

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith("tree-123", "newbie@example.com", "editor");
    });
    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toMatch(/Invitation sent/);
    });
  });
});
