/**
 * Vitest для /familysearch/connect (Phase 5.1).
 *
 * Покрывает базовый UX: показ статуса (connected/disconnected),
 * клик по «Connect» → start mutation → window.location.href редирект,
 * парсинг ?status=error&reason=... в человекочитаемое сообщение.
 *
 * Сетевые вызовы api.ts мокаются через vi.mock — нет реального fetch.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// next/navigation hooks — заглушки до import'а компонента.
const mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useParams: () => ({}),
}));

// next/link — простой <a>-stub, чтобы не тащить Next runtime.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const apiMocks = vi.hoisted(() => ({
  fetchFamilySearchAccount: vi.fn(),
  startFamilySearchOAuth: vi.fn(),
  disconnectFamilySearch: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchFamilySearchAccount: apiMocks.fetchFamilySearchAccount,
    startFamilySearchOAuth: apiMocks.startFamilySearchOAuth,
    disconnectFamilySearch: apiMocks.disconnectFamilySearch,
  };
});

import FamilySearchConnectPage from "@/app/familysearch/connect/page";

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  apiMocks.fetchFamilySearchAccount.mockReset();
  apiMocks.startFamilySearchOAuth.mockReset();
  apiMocks.disconnectFamilySearch.mockReset();
  // Очищаем search-params между тестами.
  for (const key of Array.from(mockSearchParams.keys())) {
    mockSearchParams.delete(key);
  }
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("FamilySearchConnectPage", () => {
  it("показывает «not connected» когда у user'а нет токена", async () => {
    apiMocks.fetchFamilySearchAccount.mockResolvedValue({
      connected: false,
      fs_user_id: null,
      scope: null,
      expires_at: null,
      needs_refresh: false,
    });

    renderWithClient(<FamilySearchConnectPage />);

    await waitFor(() => {
      expect(screen.getByText(/not connected yet/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /connect familysearch/i })).toBeInTheDocument();
  });

  it("при connected показывает FS user id и кнопку Disconnect", async () => {
    apiMocks.fetchFamilySearchAccount.mockResolvedValue({
      connected: true,
      fs_user_id: "MMMM-MMM",
      scope: "openid profile",
      expires_at: "2030-01-01T00:00:00Z",
      needs_refresh: false,
    });

    renderWithClient(<FamilySearchConnectPage />);

    await waitFor(() => {
      expect(screen.getByText(/MMMM-MMM/)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /disconnect/i })).toBeInTheDocument();
  });

  it("клик по Connect редиректит браузер на authorize_url", async () => {
    apiMocks.fetchFamilySearchAccount.mockResolvedValue({
      connected: false,
      fs_user_id: null,
      scope: null,
      expires_at: null,
      needs_refresh: false,
    });
    apiMocks.startFamilySearchOAuth.mockResolvedValue({
      authorize_url: "https://identbeta.familysearch.org/oauth?code_challenge=abc",
      state: "state-xyz",
      expires_in: 600,
    });

    // window.location.href в jsdom доступен на write — присваиваем в spy-обёртку.
    const locationSpy = vi.spyOn(window, "location", "get").mockReturnValue({
      ...window.location,
      assign: vi.fn(),
      replace: vi.fn(),
      reload: vi.fn(),
      href: "",
    } as unknown as Location);
    // Проще: подменим href на пишущий setter через Object.defineProperty.
    locationSpy.mockRestore();
    let assignedUrl = "";
    Object.defineProperty(window, "location", {
      writable: true,
      value: {
        ...window.location,
        get href() {
          return assignedUrl;
        },
        set href(value: string) {
          assignedUrl = value;
        },
      },
    });

    renderWithClient(<FamilySearchConnectPage />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /connect familysearch/i })).toBeEnabled();
    });

    await userEvent.click(screen.getByRole("button", { name: /connect familysearch/i }));

    await waitFor(() => {
      expect(apiMocks.startFamilySearchOAuth).toHaveBeenCalledTimes(1);
      expect(assignedUrl).toBe("https://identbeta.familysearch.org/oauth?code_challenge=abc");
    });
  });

  it("показывает понятное сообщение при ?status=error&reason=state_mismatch", async () => {
    mockSearchParams.set("status", "error");
    mockSearchParams.set("reason", "state_mismatch");
    apiMocks.fetchFamilySearchAccount.mockResolvedValue({
      connected: false,
      fs_user_id: null,
      scope: null,
      expires_at: null,
      needs_refresh: false,
    });

    renderWithClient(<FamilySearchConnectPage />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/security check failed/i);
    });
  });
});
