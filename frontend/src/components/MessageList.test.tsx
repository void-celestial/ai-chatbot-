import { afterEach, describe, expect, it } from "vitest";
import type { Message } from "../types";
import { MessageList } from "./MessageList";
import type { RenderResult } from "../test/harness";
import { render, textOf } from "../test/harness";

const CREATED_AT = "2026-10-21T10:00:00.000Z";
const CONVERSATION_ID = "c".repeat(32);
const SOURCE_MESSAGE_ID = "d".repeat(32);

function message(overrides: Partial<Message> = {}): Message {
  return {
    id: "m".repeat(32),
    conversation_id: CONVERSATION_ID,
    seq: 1,
    role: "assistant",
    content: "I remember your sister Mara lives in Oslo.",
    created_at: CREATED_AT,
    model_tag: "fake-model:latest",
    generation_status: "complete",
    metadata: null,
    ...overrides,
  };
}

const withTrace: Message = message({
  metadata: {
    memory_evidence: [
      {
        id: "a".repeat(32),
        kind: "fact",
        statement: "The user's sister Mara lives in Oslo.",
        origin: "user_stated",
        status: "accepted",
        source_conversation_id: CONVERSATION_ID,
        source_message_ids: [SOURCE_MESSAGE_ID],
      },
    ],
  },
});

let view: RenderResult | null = null;

afterEach(() => {
  view?.unmount();
  view = null;
  document.body.innerHTML = "";
});

describe("memory source disclosure", () => {
  it("lists the memory record and validated source ids under the reply", () => {
    view = render(
      <MessageList
        messages={[withTrace]}
        streamedContent={null}
        isLoading={false}
        conversationTitle="Trip planning"
      />,
    );

    const text = textOf(view.container);
    expect(text).toContain("Memory used (1)");
    expect(text).toContain("The user's sister Mara lives in Oslo.");
    expect(text).toContain("Fact");
    expect(text).toContain("Stated by you");
    expect(text).toContain("Active");
    expect(text).toContain(CONVERSATION_ID);
    expect(text).toContain(SOURCE_MESSAGE_ID);
    expect(text).toContain("supplied as evidence for this reply");
  });

  it("does not show a disclosure when no memory was supplied", () => {
    view = render(
      <MessageList
        messages={[message()]}
        streamedContent={null}
        isLoading={false}
        conversationTitle="Trip planning"
      />,
    );

    expect(textOf(view.container)).not.toContain("Memory used");
  });

  it("never renders a trace on a user message", () => {
    view = render(
      <MessageList
        messages={[{ ...withTrace, id: "u".repeat(32), role: "user", content: "Hello" }]}
        streamedContent={null}
        isLoading={false}
        conversationTitle="Trip planning"
      />,
    );

    expect(textOf(view.container)).not.toContain("Memory used");
  });
});
