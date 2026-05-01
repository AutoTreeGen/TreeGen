import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageBubble } from "./MessageBubble";

/**
 * Phase 10.7c — MessageBubble unit tests.
 *
 * Покрытие:
 *  - user-сообщение рендерит контент в правильном wrapper'е (data-testid).
 *  - assistant-сообщение со streaming=true рендерит caret indicator.
 *  - references (Phase 10.7b ego-resolver hits) рендерятся как badge-список.
 */

describe("MessageBubble", () => {
  it("renders a user message with its content", () => {
    render(<MessageBubble kind="user" content="Tell me about my wife." />);
    const bubble = screen.getByTestId("chat-bubble-user");
    expect(bubble).toBeInTheDocument();
    expect(bubble).toHaveTextContent("Tell me about my wife.");
  });

  it("shows streaming caret on an in-progress assistant bubble", () => {
    render(<MessageBubble kind="assistant" content="Your wife is" streaming />);
    expect(screen.getByTestId("chat-bubble-assistant")).toHaveTextContent(/Your wife is/);
    expect(screen.getByTestId("chat-bubble-caret")).toBeInTheDocument();
  });

  it("renders reference badges when refs are passed", () => {
    render(
      <MessageBubble
        kind="user"
        content="Tell me about my wife."
        references={[{ person_id: "person-1", mention_text: "my wife", confidence: 1.0 }]}
      />,
    );
    const refsBlock = screen.getByTestId("chat-bubble-references");
    expect(refsBlock).toHaveTextContent("my wife");
  });

  it("hides caret on completed assistant message", () => {
    render(<MessageBubble kind="assistant" content="Done." />);
    expect(screen.queryByTestId("chat-bubble-caret")).not.toBeInTheDocument();
  });
});
