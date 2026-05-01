import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/lib/api";
import * as chatApi from "@/lib/chat/api";
import type { ChatFrame } from "@/lib/chat/api";
import enMessages from "../../../../../messages/en.json";

import ChatPage from "./page";

/**
 * Phase 10.7c — chat page integration test.
 *
 * Mock'и:
 *  - useParams → tree-1 (next/navigation).
 *  - fetchTreeOwnerPerson + fetchPerson → anchor "Vladimir Z".
 *  - streamChatTurn → детерминированный async-generator кадров.
 *
 * Покрытие:
 *  - empty state с anchor сразу рендерится system-bubble'ом hint'а.
 *  - submit input → user-bubble появляется, токены аккумулируются в
 *    assistant-bubble, done-кадр финализирует session-id, sidebar
 *    показывает referenced person.
 */

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "tree-1" }),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/trees/tree-1/chat",
  useSearchParams: () => new URLSearchParams(),
}));

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

beforeEach(() => {
  vi.restoreAllMocks();

  // Phase 10.7d — sessions sidebar fetches list; default mock = empty.
  vi.spyOn(chatApi, "listChatSessions").mockResolvedValue({
    tree_id: "tree-1",
    total: 0,
    limit: 30,
    offset: 0,
    items: [],
  });
  vi.spyOn(chatApi, "loadChatMessages").mockResolvedValue({
    session_id: "sess-1",
    total: 0,
    limit: 200,
    offset: 0,
    items: [],
  });

  vi.spyOn(api, "fetchTreeOwnerPerson").mockResolvedValue({
    tree_id: "tree-1",
    owner_person_id: "person-anchor",
  });

  vi.spyOn(api, "fetchPerson").mockResolvedValue({
    id: "person-anchor",
    tree_id: "tree-1",
    gedcom_xref: null,
    sex: "M",
    status: "active",
    confidence_score: 1.0,
    names: [{ id: "name-1", given_name: "Vladimir", surname: "Z", sort_order: 0 }],
    events: [],
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function* makeStreamFrames(frames: ChatFrame[]): AsyncGenerator<ChatFrame, void, void> {
  for (const f of frames) {
    yield f;
  }
}

describe("ChatPage (/trees/[id]/chat)", () => {
  it("renders header + initial empty-state hint with anchor loaded", async () => {
    render(wrap(<ChatPage />));
    expect(screen.getByText(/Chat with your tree/i)).toBeInTheDocument();
    // System-bubble suggesting first prompts.
    expect(screen.getByTestId("chat-bubble-system")).toHaveTextContent(/Try:/i);
    // Sidebar shows anchor label after fetchPerson resolves.
    await waitFor(() => {
      expect(screen.getByTestId("chat-context-anchor")).toHaveTextContent("Vladimir Z");
    });
  });

  it("submits a message and accumulates streamed tokens into assistant bubble", async () => {
    const streamSpy = vi.spyOn(chatApi, "streamChatTurn").mockImplementation(() =>
      makeStreamFrames([
        { type: "session", session_id: "sess-1", anchor_person_id: "person-anchor" },
        { type: "token", delta: "Your wife " },
        { type: "token", delta: "is Olga." },
        {
          type: "done",
          message_id: "msg-1",
          referenced_persons: [
            { person_id: "person-spouse", mention_text: "my wife", confidence: 1.0 },
          ],
        },
      ]),
    );

    render(wrap(<ChatPage />));
    // Wait for owner-person fetch so input is enabled.
    await waitFor(() => {
      expect(screen.getByTestId("chat-input")).not.toBeDisabled();
    });

    const input = screen.getByTestId("chat-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Tell me about my wife." } });
    fireEvent.click(screen.getByTestId("chat-send"));

    // User bubble appears immediately.
    await waitFor(() => {
      expect(screen.getByTestId("chat-bubble-user")).toHaveTextContent("Tell me about my wife.");
    });

    // Assistant bubble accumulates the streamed text.
    await waitFor(() => {
      expect(screen.getByTestId("chat-bubble-assistant")).toHaveTextContent("Your wife is Olga.");
    });

    // Sidebar shows the referenced person.
    await waitFor(() => {
      expect(screen.getByTestId("chat-context-people-list")).toHaveTextContent("my wife");
    });

    expect(streamSpy).toHaveBeenCalledWith("tree-1", {
      session_id: null,
      message: "Tell me about my wife.",
      anchor_person_id: "person-anchor",
    });
  });
});
