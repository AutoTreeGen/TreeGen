import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import enMessages from "../../../messages/en.json";

import { SessionList } from "./SessionList";

/**
 * Phase 10.7d — SessionList unit tests.
 *
 * Покрытие:
 *  - empty state — рендерит plain "no chats yet".
 *  - loading state — placeholder вместо списка.
 *  - non-empty — все sessions, селектор active, click эмитит callback.
 *  - "New chat" клик зовёт onNewSession.
 *  - untitled session показывает fallback из i18n.
 */

function wrap(ui: ReactNode) {
  return (
    <NextIntlClientProvider locale="en" messages={enMessages}>
      {ui}
    </NextIntlClientProvider>
  );
}

const _SESSIONS = [
  {
    id: "sess-1",
    tree_id: "tree-1",
    anchor_person_id: "person-1",
    title: "First chat",
    created_at: "2026-05-02T10:00:00Z",
    updated_at: "2026-05-02T10:00:00Z",
    message_count: 3,
    last_message_at: "2026-05-02T10:01:00Z",
  },
  {
    id: "sess-2",
    tree_id: "tree-1",
    anchor_person_id: "person-1",
    title: null,
    created_at: "2026-05-01T09:00:00Z",
    updated_at: "2026-05-01T09:00:00Z",
    message_count: 0,
    last_message_at: null,
  },
];

describe("SessionList", () => {
  it("renders empty-state when no sessions", () => {
    render(
      wrap(
        <SessionList
          sessions={[]}
          activeSessionId={null}
          onSelectSession={vi.fn()}
          onNewSession={vi.fn()}
        />,
      ),
    );
    expect(screen.getByTestId("chat-session-list-empty")).toBeInTheDocument();
  });

  it("shows loading state when loading=true", () => {
    render(
      wrap(
        <SessionList
          sessions={[]}
          activeSessionId={null}
          loading
          onSelectSession={vi.fn()}
          onNewSession={vi.fn()}
        />,
      ),
    );
    expect(screen.getByTestId("chat-session-list-loading")).toBeInTheDocument();
  });

  it("renders sessions and falls back to untitled label", () => {
    render(
      wrap(
        <SessionList
          sessions={_SESSIONS}
          activeSessionId={null}
          onSelectSession={vi.fn()}
          onNewSession={vi.fn()}
        />,
      ),
    );
    expect(screen.getByTestId("chat-session-sess-1")).toHaveTextContent("First chat");
    expect(screen.getByTestId("chat-session-sess-2")).toHaveTextContent("Untitled chat");
  });

  it("marks active session via data-active attr", () => {
    render(
      wrap(
        <SessionList
          sessions={_SESSIONS}
          activeSessionId="sess-2"
          onSelectSession={vi.fn()}
          onNewSession={vi.fn()}
        />,
      ),
    );
    expect(screen.getByTestId("chat-session-sess-2")).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId("chat-session-sess-1")).toHaveAttribute("data-active", "false");
  });

  it("calls onSelectSession with id on click", () => {
    const onSelect = vi.fn();
    render(
      wrap(
        <SessionList
          sessions={_SESSIONS}
          activeSessionId={null}
          onSelectSession={onSelect}
          onNewSession={vi.fn()}
        />,
      ),
    );
    fireEvent.click(screen.getByTestId("chat-session-sess-1"));
    expect(onSelect).toHaveBeenCalledWith("sess-1");
  });

  it("calls onNewSession when 'New' button clicked", () => {
    const onNew = vi.fn();
    render(
      wrap(
        <SessionList
          sessions={_SESSIONS}
          activeSessionId={null}
          onSelectSession={vi.fn()}
          onNewSession={onNew}
        />,
      ),
    );
    fireEvent.click(screen.getByTestId("chat-new-session"));
    expect(onNew).toHaveBeenCalledTimes(1);
  });
});
