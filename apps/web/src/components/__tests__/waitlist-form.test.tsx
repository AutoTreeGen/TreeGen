import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WaitlistForm } from "@/components/waitlist-form";
import enMessages from "../../../messages/en.json";

/**
 * Тесты waitlist-формы (Phase 4.12 / ADR-0035).
 *
 * Покрытие:
 *   - валидный email → POST /api/waitlist → success state;
 *   - невалидный email → клиентская ошибка, fetch не вызывается;
 *   - не-200 ответ → generic error;
 *   - сетевой fail → generic error.
 */
function renderWithIntl(ui: React.ReactElement) {
  return render(
    <NextIntlClientProvider locale="en" messages={enMessages}>
      {ui}
    </NextIntlClientProvider>,
  );
}

describe("WaitlistForm", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("submits a valid email and shows success state", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) });
    const user = userEvent.setup();
    renderWithIntl(<WaitlistForm />);

    await user.type(screen.getByPlaceholderText(/you@example.com/i), "ok@example.com");
    await user.click(screen.getByRole("button", { name: /join waitlist/i }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(/Thanks/);
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/waitlist",
      expect.objectContaining({ method: "POST" }),
    );
    const body = JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string);
    expect(body.email).toBe("ok@example.com");
  });

  it("rejects invalid email client-side and does not fetch", async () => {
    const user = userEvent.setup();
    renderWithIntl(<WaitlistForm />);

    // jsdom не валидирует type=email/required, но и не submit'ит форму
    // через user.click reliably — поэтому печатаем bad value и явно
    // дёргаем fireEvent.submit для контроля над тем, что reducer-ветка
    // «invalid email» отрабатывается.
    const input = screen.getByPlaceholderText(/you@example.com/i);
    input.removeAttribute("type");
    input.removeAttribute("required");
    await user.type(input, "not-an-email");
    const form = input.closest("form");
    if (form) fireEvent.submit(form);

    expect(await screen.findByRole("alert")).toHaveTextContent(/valid email/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows generic error on non-2xx response", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 502, json: async () => ({}) });
    const user = userEvent.setup();
    renderWithIntl(<WaitlistForm />);

    await user.type(screen.getByPlaceholderText(/you@example.com/i), "ok@example.com");
    await user.click(screen.getByRole("button", { name: /join waitlist/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/something went wrong/i);
    });
  });

  it("shows generic error on network failure", async () => {
    fetchMock.mockRejectedValueOnce(new Error("network down"));
    const user = userEvent.setup();
    renderWithIntl(<WaitlistForm />);

    await user.type(screen.getByPlaceholderText(/you@example.com/i), "ok@example.com");
    await user.click(screen.getByRole("button", { name: /join waitlist/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/something went wrong/i);
    });
  });
});
