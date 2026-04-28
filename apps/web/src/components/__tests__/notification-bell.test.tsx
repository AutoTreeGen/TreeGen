import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationBell } from "@/components/notification-bell";
import * as api from "@/lib/notifications-api";

/**
 * Vitest для bell + dropdown (Phase 8.0 wire-up).
 *
 * Mock'аем ``notifications-api`` целиком — компонент тестируется в
 * изоляции от сети. ``window.location.href`` подменяем, чтобы click
 * по deep-link'у не пытался реально навигировать в jsdom.
 */

function wrapper({ children }: { children: ReactNode }) {
  // Свежий QueryClient на каждый тест — чтобы кэш не утекал.
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const sampleNotification: api.NotificationSummary = {
  id: "11111111-1111-1111-1111-111111111111",
  event_type: "hypothesis_pending_review",
  payload: {
    hypothesis_id: "22222222-2222-2222-2222-222222222222",
    tree_id: "33333333-3333-3333-3333-333333333333",
    composite_score: 0.91,
    hypothesis_type: "same_person",
    ref_id: "22222222-2222-2222-2222-222222222222",
  },
  delivered_at: "2026-04-28T10:00:00Z",
  read_at: null,
  created_at: "2026-04-28T10:00:00Z",
};

describe("NotificationBell", () => {
  let originalLocation: Location;

  beforeEach(() => {
    // jsdom не даёт писать в window.location напрямую — заменяем целиком.
    originalLocation = window.location;
    Object.defineProperty(window, "location", {
      writable: true,
      value: { href: "" } as Location,
    });
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      writable: true,
      value: originalLocation,
    });
    vi.restoreAllMocks();
  });

  it("renders no badge when there are zero unread notifications", async () => {
    vi.spyOn(api, "fetchNotifications").mockResolvedValue({
      user_id: 1,
      total: 0,
      unread: 0,
      limit: 10,
      offset: 0,
      items: [],
    });

    render(<NotificationBell />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("notification-bell-button")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("notification-bell-badge")).not.toBeInTheDocument();
  });

  it("shows unread count badge", async () => {
    vi.spyOn(api, "fetchNotifications").mockResolvedValue({
      user_id: 1,
      total: 7,
      unread: 7,
      limit: 10,
      offset: 0,
      items: [sampleNotification],
    });

    render(<NotificationBell />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("notification-bell-badge")).toHaveTextContent("7");
    });
  });

  it("clamps badge to 99+ when count exceeds 99", async () => {
    vi.spyOn(api, "fetchNotifications").mockResolvedValue({
      user_id: 1,
      total: 250,
      unread: 250,
      limit: 10,
      offset: 0,
      items: [sampleNotification],
    });

    render(<NotificationBell />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("notification-bell-badge")).toHaveTextContent("99+");
    });
  });

  it("opens dropdown on click and lists unread items", async () => {
    vi.spyOn(api, "fetchNotifications").mockResolvedValue({
      user_id: 1,
      total: 1,
      unread: 1,
      limit: 10,
      offset: 0,
      items: [sampleNotification],
    });

    render(<NotificationBell />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("notification-bell-badge")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("notification-bell-button"));

    expect(screen.getByTestId("notification-bell-dropdown")).toBeInTheDocument();
    expect(screen.getByText("New hypothesis to review")).toBeInTheDocument();
  });

  it("marks notification read and navigates to deep-link on item click", async () => {
    vi.spyOn(api, "fetchNotifications").mockResolvedValue({
      user_id: 1,
      total: 1,
      unread: 1,
      limit: 10,
      offset: 0,
      items: [sampleNotification],
    });
    const markSpy = vi.spyOn(api, "markNotificationRead").mockResolvedValue({
      id: sampleNotification.id,
      read_at: "2026-04-28T10:05:00Z",
    });

    render(<NotificationBell />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("notification-bell-badge")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("notification-bell-button"));
    fireEvent.click(screen.getByTestId("notification-bell-item"));

    await waitFor(() => {
      expect(markSpy).toHaveBeenCalledWith(sampleNotification.id);
    });
    // Hypothesis-link → /hypotheses/{id}
    expect(window.location.href).toBe("/hypotheses/22222222-2222-2222-2222-222222222222");
  });

  it("renders empty-state when there are zero items even with badge open", async () => {
    vi.spyOn(api, "fetchNotifications").mockResolvedValue({
      user_id: 1,
      total: 0,
      unread: 0,
      limit: 10,
      offset: 0,
      items: [],
    });

    render(<NotificationBell />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("notification-bell-button")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("notification-bell-button"));

    expect(screen.getByText(/all caught up/i)).toBeInTheDocument();
  });
});
