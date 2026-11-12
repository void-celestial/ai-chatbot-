import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as client from "./api/client";
import type { RenderResult } from "./test/harness";
import { buttonContaining, buttonExact, click, clickAsync, flush, pressEscape, render, textOf } from "./test/harness";

vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return {
    ...actual,
    fetchHealth: vi.fn(),
    listConversations: vi.fn(),
    listMessages: vi.fn(),
    listMemories: vi.fn(),
    listVoiceNotes: vi.fn(),
    deleteConversation: vi.fn(),
    createConversation: vi.fn(),
    exportData: vi.fn(),
  };
});

let view: RenderResult | null = null;

beforeEach(() => {
  vi.mocked(client.fetchHealth).mockResolvedValue({
    status: "ok",
    ollama: { reachable: true, error: null },
    model: { tag: "fake-model:latest", available: true },
  });
  vi.mocked(client.listConversations).mockResolvedValue([]);
  vi.mocked(client.listMessages).mockResolvedValue([]);
  vi.mocked(client.deleteConversation).mockResolvedValue(undefined);
  vi.mocked(client.exportData).mockResolvedValue({
    format: "companion-export",
    format_version: 1,
    exported_at: "2026-10-22T12:00:00.000Z",
    scope: { includes: [], excludes: [] },
    counts: { conversations: 0, messages: 0, memories: 0, memory_history: 0 },
    persona: null,
    conversations: [],
    messages: [],
    memories: [],
    memory_history: [],
  });
  vi.mocked(client.listMemories).mockResolvedValue({
    items: [],
    total: 0,
    limit: 20,
    offset: 0,
  });
  vi.mocked(client.listVoiceNotes).mockResolvedValue([]);
});

afterEach(() => {
  view?.unmount();
  view = null;
  vi.clearAllMocks();
  document.body.innerHTML = "";
  document.documentElement.classList.remove("dark");
  localStorage.clear();
});

describe("voice notes access", () => {
  it("opens voice notes from the sidebar and returns to chat", async () => {
    view = render(<App />);
    await flush();

    await clickAsync(buttonExact(view.container, "Voice notes"));
    await flush();

    expect(textOf(view.container)).toContain("Record or import audio");
    expect(textOf(view.container)).toContain("No voice notes yet");

    await clickAsync(buttonExact(view.container, "Back to chat"));
    await flush();

    expect(textOf(view.container)).toContain("This conversation is just beginning.");
  });
});

describe("memory screen access", () => {
  it("opens the memory review screen from the sidebar and returns to chat", async () => {
    view = render(<App />);
    await flush();
    expect(textOf(view.container)).toContain("No conversations yet.");

    await clickAsync(buttonExact(view.container, "Memories"));
    await flush();

    expect(textOf(view.container)).toContain("Review what your companion remembers.");
    expect(textOf(view.container)).toContain("No memories have been saved yet.");

    await clickAsync(buttonExact(view.container, "Back to chat"));
    await flush();

    expect(textOf(view.container)).toContain("This conversation is just beginning.");
  });

  it("deletes the active conversation after confirmation and explains memory effects", async () => {
    const conversation = {
      id: "conversation-1",
      title: "Old chat",
      status: "active",
      created_at: "2026-10-22T10:00:00.000Z",
      updated_at: "2026-10-22T10:00:00.000Z",
      message_count: 2,
    };
    vi.mocked(client.listConversations)
      .mockResolvedValueOnce([conversation])
      .mockResolvedValue([]);
    view = render(<App />);
    await flush();

    expect(textOf(view.container)).toContain("Old chat");
    await clickAsync(buttonExact(view.container, "Delete…"));
    expect(textOf(view.container)).toContain("Delete this conversation?");
    expect(textOf(view.container)).toContain("Source missing");
    expect(textOf(view.container)).toContain("not complete erasure");

    await clickAsync(buttonExact(view.container, "Delete conversation"));

    expect(vi.mocked(client.deleteConversation)).toHaveBeenCalledWith("conversation-1");
    await flush();
    expect(textOf(view.container)).toContain("No conversations yet.");
    expect(textOf(view.container)).not.toContain("Old chat");
  });

  it("closes the delete confirmation with Escape without deleting", async () => {
    const conversation = {
      id: "conversation-1",
      title: "Old chat",
      status: "active",
      created_at: "2026-10-22T10:00:00.000Z",
      updated_at: "2026-10-22T10:00:00.000Z",
      message_count: 0,
    };
    vi.mocked(client.listConversations).mockResolvedValueOnce([conversation]).mockResolvedValue([conversation]);
    view = render(<App />);
    await flush();

    await clickAsync(buttonExact(view.container, "Delete…"));
    const panel = view.container.querySelector(".chat-delete-panel");
    if (!panel) throw new Error("delete panel did not open");

    pressEscape(panel);
    await flush();

    expect(textOf(view.container)).not.toContain("Delete this conversation?");
    expect(vi.mocked(client.deleteConversation)).not.toHaveBeenCalled();
  });

  it("downloads the JSON export from the sidebar", async () => {
    const createObjectURL = vi.fn(() => "blob:export");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    const anchorClick = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    view = render(<App />);
    await flush();
    await clickAsync(buttonExact(view.container, "Export data"));
    await flush();

    expect(vi.mocked(client.exportData)).toHaveBeenCalledTimes(1);
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:export");
    expect(anchorClick).toHaveBeenCalled();
    anchorClick.mockRestore();
  });

  it("shows an export failure without claiming success", async () => {
    vi.mocked(client.exportData).mockRejectedValueOnce(
      new client.ApiRequestError("http_500", "Could not export local data."),
    );

    view = render(<App />);
    await flush();
    await clickAsync(buttonExact(view.container, "Export data"));
    await flush();

    expect(textOf(view.container)).toContain("Could not export local data.");
    expect(buttonExact(view.container, "Export data")).toBeTruthy();
  });

  it("keeps the light/dark theme toggle working from a dark-first default", async () => {
    view = render(<App />);
    await flush();
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    const toggle = view.container.querySelector<HTMLButtonElement>(
      'button[aria-label="Switch light/dark theme"]',
    );
    if (!toggle) throw new Error("theme toggle is missing");
    click(toggle);

    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(textOf(view.container)).toContain("Companion");
  });
});

// ------------------------------------------------- conversation retrieval ---

const CONVERSATION_A = {
  id: "conversation-a",
  title: "Morning check-in",
  status: "active",
  created_at: "2026-10-22T08:00:00.000Z",
  updated_at: "2026-10-22T09:00:00.000Z",
  message_count: 2,
};

const CONVERSATION_B = {
  id: "conversation-b",
  title: "Evening notes",
  status: "active",
  created_at: "2026-10-21T08:00:00.000Z",
  updated_at: "2026-10-21T09:00:00.000Z",
  message_count: 1,
};

function messageFor(conversationId: string, content: string, seq = 1) {
  return {
    id: `${conversationId}-${seq}`,
    conversation_id: conversationId,
    seq,
    role: "assistant" as const,
    content,
    created_at: "2026-10-22T09:00:00.000Z",
    model_tag: "fake-model:latest",
    generation_status: "complete",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

describe("conversation retrieval", () => {
  it("shows a loading state before the conversation list resolves", async () => {
    const pending = deferred<Awaited<ReturnType<typeof client.listConversations>>>();
    vi.mocked(client.listConversations).mockReturnValueOnce(pending.promise);

    view = render(<App />);

    expect(textOf(view.container)).toContain("Loading conversations…");
    expect(textOf(view.container)).not.toContain("No conversations yet.");

    pending.resolve([]);
    await flush();

    expect(textOf(view.container)).toContain("No conversations yet.");
  });

  it("loads the messages of the initially selected conversation", async () => {
    vi.mocked(client.listConversations).mockResolvedValue([CONVERSATION_A]);
    vi.mocked(client.listMessages).mockResolvedValue([
      messageFor("conversation-a", "Welcome back — here is where we left off."),
    ]);

    view = render(<App />);
    await flush();

    expect(vi.mocked(client.listMessages)).toHaveBeenCalledWith("conversation-a");
    expect(textOf(view.container)).toContain("Welcome back — here is where we left off.");
    expect(textOf(view.container)).not.toContain("This conversation is just beginning.");
  });

  it("reports an unreachable backend instead of a false empty history, and retries", async () => {
    vi.mocked(client.listConversations).mockRejectedValueOnce(
      new TypeError("Failed to fetch"),
    );

    view = render(<App />);
    await flush();

    expect(textOf(view.container)).toContain("Could not reach the companion backend");
    expect(textOf(view.container)).not.toContain("No conversations yet.");

    vi.mocked(client.listConversations).mockResolvedValue([CONVERSATION_A]);
    vi.mocked(client.listMessages).mockResolvedValue([]);
    await clickAsync(buttonExact(view.container, "Try again"));
    await flush();

    expect(textOf(view.container)).toContain("Morning check-in");
    expect(textOf(view.container)).not.toContain("Could not reach the companion backend");
  });

  it("reports a backend error status from the list endpoint", async () => {
    vi.mocked(client.listConversations).mockRejectedValueOnce(
      new client.ApiRequestError("http_500", "Could not read stored conversations."),
    );

    view = render(<App />);
    await flush();

    expect(textOf(view.container)).toContain("Could not read stored conversations.");
    expect(textOf(view.container)).not.toContain("No conversations yet.");
  });

  it("loads only the messages of the conversation that was selected", async () => {
    vi.mocked(client.listConversations).mockResolvedValue([CONVERSATION_A, CONVERSATION_B]);
    vi.mocked(client.listMessages).mockImplementation(async (conversationId: string) =>
      conversationId === "conversation-b"
        ? [messageFor("conversation-b", "Evening notes content")]
        : [messageFor("conversation-a", "Morning check-in content")],
    );

    view = render(<App />);
    await flush();
    expect(textOf(view.container)).toContain("Morning check-in content");

    await clickAsync(buttonContaining(view.container, "Evening notes"));
    await flush();

    expect(vi.mocked(client.listMessages)).toHaveBeenLastCalledWith("conversation-b");
    expect(textOf(view.container)).toContain("Evening notes content");
    expect(textOf(view.container)).not.toContain("Morning check-in content");
  });

  it("never mixes conversations when a slow load finishes after a newer selection", async () => {
    const slow = deferred<Awaited<ReturnType<typeof client.listMessages>>>();
    vi.mocked(client.listConversations).mockResolvedValue([CONVERSATION_A, CONVERSATION_B]);
    vi.mocked(client.listMessages).mockImplementation((conversationId: string) => {
      if (conversationId === "conversation-a") return slow.promise;
      return Promise.resolve([messageFor("conversation-b", "Evening notes content")]);
    });

    view = render(<App />);
    await flush();

    await clickAsync(buttonContaining(view.container, "Evening notes"));
    await flush();
    expect(textOf(view.container)).toContain("Evening notes content");

    // The stale first request settles afterwards and must not overwrite the view.
    slow.resolve([messageFor("conversation-a", "Late morning content")]);
    await flush();

    expect(textOf(view.container)).toContain("Evening notes content");
    expect(textOf(view.container)).not.toContain("Late morning content");
  });

  it("shows a recoverable error when the selected conversation's messages fail", async () => {
    vi.mocked(client.listConversations).mockResolvedValue([CONVERSATION_A]);
    vi.mocked(client.listMessages).mockRejectedValue(
      new client.ApiRequestError("http_500", "Could not read stored messages."),
    );

    view = render(<App />);
    await flush();

    expect(textOf(view.container)).toContain("Couldn't load this conversation.");
    expect(textOf(view.container)).toContain("Could not read stored messages.");
    expect(textOf(view.container)).not.toContain("This conversation is just beginning.");

    vi.mocked(client.listMessages).mockResolvedValue([
      messageFor("conversation-a", "Back after a retry."),
    ]);
    await clickAsync(buttonExact(view.container, "Try again"));
    await flush();

    expect(textOf(view.container)).toContain("Back after a retry.");
    expect(textOf(view.container)).not.toContain("Couldn't load this conversation.");
  });

  it("drops a stale message load when a new conversation is created", async () => {
    const slow = deferred<Awaited<ReturnType<typeof client.listMessages>>>();
    vi.mocked(client.listConversations).mockResolvedValue([CONVERSATION_A]);
    vi.mocked(client.listMessages).mockReturnValue(slow.promise);
    vi.mocked(client.createConversation).mockResolvedValue({
      id: "conversation-new",
      title: "New conversation",
      status: "active",
      created_at: "2026-10-25T10:00:00.000Z",
      updated_at: "2026-10-25T10:00:00.000Z",
      message_count: 0,
    });

    view = render(<App />);
    await flush();
    await clickAsync(buttonContaining(view.container, "New conversation"));
    await flush();

    slow.resolve([messageFor("conversation-a", "Stale morning content")]);
    await flush();

    expect(textOf(view.container)).not.toContain("Stale morning content");
    expect(textOf(view.container)).toContain("This conversation is just beginning.");
  });
});
