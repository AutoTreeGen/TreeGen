/**
 * Vitest для /settings page (Phase 4.10b, ADR-0038).
 *
 * Покрывает:
 * - render всех 3 tabs (Profile / Sessions / Danger zone)
 * - tab switching
 * - profile save → PATCH /users/me
 * - delete-account modal flow: open → type wrong email → button disabled
 *   → type correct → enabled → click → POST /users/me/erasure-request
 * - export request → POST /users/me/export-request
 *
 * Clerk и сетевые вызовы мокаются. Sessions tab покрыт smoke'ом
 * (rendered + кнопки видны); полный flow через Clerk API — manual smoke,
 * см. PR description.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mocks ------------------------------------------------------------------

// Мокаем Clerk-хуки: settings page вызывает useUser(); тесту достаточно
// stub'а с минимальной формой user-объекта.
const mockClerkUser = {
  unsafeMetadata: {},
  update: vi.fn().mockResolvedValue({}),
  getSessions: vi.fn().mockResolvedValue([]),
};

vi.mock("@clerk/nextjs", () => ({
  useUser: () => ({ user: mockClerkUser, isLoaded: true }),
  useAuth: () => ({ getToken: vi.fn().mockResolvedValue("test-token") }),
}));

// Мокаем user-settings-api: тестируем UI flow, не сетевую логику.
const mockFetchMe = vi.fn();
const mockFetchMyRequests = vi.fn();
const mockUpdateMe = vi.fn();
const mockRequestErasure = vi.fn();
const mockRequestExport = vi.fn();

vi.mock("@/lib/user-settings-api", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/user-settings-api")>("@/lib/user-settings-api");
  return {
    ...actual,
    fetchMe: () => mockFetchMe(),
    fetchMyRequests: () => mockFetchMyRequests(),
    updateMe: (body: unknown) => mockUpdateMe(body),
    requestErasure: (body: unknown) => mockRequestErasure(body),
    requestExport: () => mockRequestExport(),
  };
});

import SettingsPage from "@/app/(authenticated)/settings/page";

const FAKE_ME = {
  id: "00000000-0000-0000-0000-000000000001",
  email: "alice@example.com",
  clerk_user_id: "user_test_alice",
  display_name: "Alice",
  locale: "en",
  timezone: null,
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SettingsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockFetchMe.mockReset().mockResolvedValue(FAKE_ME);
  mockFetchMyRequests.mockReset().mockResolvedValue({ user_id: FAKE_ME.id, items: [] });
  mockUpdateMe.mockReset().mockResolvedValue(FAKE_ME);
  mockRequestErasure
    .mockReset()
    .mockResolvedValue({ request_id: "req-1", kind: "erasure", status: "pending" });
  mockRequestExport
    .mockReset()
    .mockResolvedValue({ request_id: "req-2", kind: "export", status: "pending" });
  mockClerkUser.update.mockReset().mockResolvedValue({});
  mockClerkUser.getSessions.mockReset().mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Tab rendering
// ---------------------------------------------------------------------------

describe("settings page — tabs", () => {
  it("renders all three tabs", async () => {
    renderPage();
    expect(await screen.findByTestId("tab-profile")).toBeInTheDocument();
    expect(screen.getByTestId("tab-sessions")).toBeInTheDocument();
    expect(screen.getByTestId("tab-danger")).toBeInTheDocument();
  });

  it("starts on Profile tab and shows display name input", async () => {
    renderPage();
    await waitFor(() =>
      expect((screen.getByTestId("profile-display-name") as HTMLInputElement).value).toBe("Alice"),
    );
  });

  it("switches to Sessions tab", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("tab-sessions");
    await user.click(screen.getByTestId("tab-sessions"));
    await waitFor(() => expect(mockClerkUser.getSessions).toHaveBeenCalled());
  });

  it("switches to Danger zone tab", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("tab-danger");
    await user.click(screen.getByTestId("tab-danger"));
    expect(await screen.findByTestId("open-delete-modal")).toBeInTheDocument();
    expect(screen.getByTestId("export-request")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Profile save
// ---------------------------------------------------------------------------

describe("profile tab — save", () => {
  it("saves changed display_name and locale (dual-writes Clerk metadata)", async () => {
    const user = userEvent.setup();
    renderPage();
    const nameInput = (await screen.findByTestId("profile-display-name")) as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "Alice Updated" } });

    const localeSelect = screen.getByTestId("profile-locale") as HTMLSelectElement;
    fireEvent.change(localeSelect, { target: { value: "ru" } });

    await user.click(screen.getByTestId("profile-save"));
    await waitFor(() =>
      expect(mockUpdateMe).toHaveBeenCalledWith(
        expect.objectContaining({
          display_name: "Alice Updated",
          locale: "ru",
        }),
      ),
    );
    // Locale dual-write — Clerk metadata тоже обновляется.
    await waitFor(() =>
      expect(mockClerkUser.update).toHaveBeenCalledWith({
        unsafeMetadata: expect.objectContaining({ locale: "ru" }),
      }),
    );
  });

  it("does nothing when no fields changed", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("profile-display-name");
    await user.click(screen.getByTestId("profile-save"));
    expect(mockUpdateMe).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Danger zone — delete modal flow
// ---------------------------------------------------------------------------

describe("danger zone — delete account modal", () => {
  it("opens modal, requires correct email, then submits erasure request", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("tab-danger"));
    await user.click(await screen.findByTestId("open-delete-modal"));

    const modal = await screen.findByTestId("delete-modal");
    expect(modal).toBeInTheDocument();

    const confirmEmail = screen.getByTestId("delete-confirm-email") as HTMLInputElement;
    const confirmBtn = screen.getByTestId("delete-confirm") as HTMLButtonElement;
    expect(confirmBtn).toBeDisabled();

    // Wrong email: still disabled.
    fireEvent.change(confirmEmail, { target: { value: "wrong@example.com" } });
    expect(confirmBtn).toBeDisabled();

    // Correct email (case-insensitive): enabled.
    fireEvent.change(confirmEmail, { target: { value: "ALICE@example.com" } });
    expect(confirmBtn).not.toBeDisabled();

    await user.click(confirmBtn);
    await waitFor(() =>
      expect(mockRequestErasure).toHaveBeenCalledWith({ confirm_email: "ALICE@example.com" }),
    );
  });
});

// ---------------------------------------------------------------------------
// Danger zone — data export
// ---------------------------------------------------------------------------

describe("danger zone — data export", () => {
  it("submits export request on click", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("tab-danger"));
    await user.click(await screen.findByTestId("export-request"));
    await waitFor(() => expect(mockRequestExport).toHaveBeenCalled());
  });

  it("hides export button when a pending export already exists", async () => {
    mockFetchMyRequests.mockResolvedValue({
      user_id: FAKE_ME.id,
      items: [
        {
          id: "rid",
          kind: "export",
          status: "pending",
          created_at: "2026-04-28T10:00:00Z",
          processed_at: null,
          error: null,
          request_metadata: {},
        },
      ],
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("tab-danger"));
    const btn = (await screen.findByTestId("export-request")) as HTMLButtonElement;
    expect(btn).toBeDisabled();
  });
});
