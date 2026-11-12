import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import type {
  Conversation,
  MemoryDetail,
  MemoryHistoryEntry,
  MemorySummary,
} from "../types";
import { MemoryView } from "./MemoryView";
import {
  buttonContaining,
  buttonExact,
  choose,
  click,
  clickAsync,
  flush,
  pressEscape,
  render,
  setValue,
  textOf,
} from "../test/harness";
import type { RenderResult } from "../test/harness";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    listMemories: vi.fn(),
    getMemory: vi.fn(),
    confirmMemory: vi.fn(),
    rejectMemory: vi.fn(),
    supersedeMemory: vi.fn(),
    correctMemory: vi.fn(),
    deleteMemory: vi.fn(),
  };
});

const MEMORY_ID = "a".repeat(32);
const CONVERSATION_ID = "c".repeat(32);
const MESSAGE_ID = "d".repeat(32);
const CREATED_AT = "2026-10-21T10:00:00.000Z";

const conversations: Conversation[] = [
  {
    id: CONVERSATION_ID,
    title: "Trip planning",
    status: "active",
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    message_count: 2,
  },
];

function summary(overrides: Partial<MemorySummary> = {}): MemorySummary {
  return {
    id: MEMORY_ID,
    kind: "fact",
    statement: "You might like Oslo.",
    subject: "Oslo",
    keywords: ["oslo", "travel"],
    origin: "inferred",
    status: "review",
    confidence: 0.5,
    provenance: {
      conversation_id: CONVERSATION_ID,
      message_ids: [MESSAGE_ID],
      valid: true,
    },
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    last_confirmed_at: null,
    ...overrides,
  };
}

function historyEntry(event: string, value: MemoryHistoryEntry["value"]): MemoryHistoryEntry {
  return {
    id: `h-${event}`,
    memory_id: MEMORY_ID,
    event,
    value,
    created_at: CREATED_AT,
  };
}

function detail(overrides: Partial<MemoryDetail> = {}): MemoryDetail {
  return {
    ...summary(),
    history: [
      historyEntry("created", {
        memory: { statement: "You might like Oslo.", status: "review" },
      }),
    ],
    ...overrides,
  };
}

const onOpenConversation = vi.fn();
const onBackToChat = vi.fn();

let view: RenderResult | null = null;

function renderView(): RenderResult {
  view = render(
    <MemoryView
      conversations={conversations}
      onBackToChat={onBackToChat}
      onOpenConversation={onOpenConversation}
    />,
  );
  return view;
}

async function openMemory(container: HTMLElement, title = "You might like Oslo"): Promise<void> {
  await clickAsync(buttonContaining(container, title));
  await flush();
}

beforeEach(() => {
  vi.mocked(client.listMemories).mockResolvedValue({
    items: [summary()],
    total: 1,
    limit: 20,
    offset: 0,
  });
  vi.mocked(client.getMemory).mockResolvedValue(detail());
  vi.mocked(client.confirmMemory).mockResolvedValue(summary({ status: "confirmed" }));
  vi.mocked(client.rejectMemory).mockResolvedValue(summary({ status: "deleted" }));
  vi.mocked(client.supersedeMemory).mockResolvedValue(summary({ status: "superseded" }));
  vi.mocked(client.correctMemory).mockResolvedValue(summary());
  vi.mocked(client.deleteMemory).mockResolvedValue(undefined);
});

afterEach(() => {
  view?.unmount();
  view = null;
  vi.clearAllMocks();
  document.body.innerHTML = "";
  localStorage.clear();
});

describe("memory list", () => {
  it("renders memories returned by the API", async () => {
    const rendered = renderView();
    await flush();

    expect(client.listMemories).toHaveBeenCalledWith({ limit: 20, offset: 0 });
    const text = textOf(rendered.container);
    expect(text).toContain("You might like Oslo.");
    expect(text).toContain("Needs review");
    expect(text).toContain("Inferred");
    expect(text).toContain("Fact");
    expect(text).toContain("1 memory");
  });

  it("filters by status and resets pagination", async () => {
    const rendered = renderView();
    await flush();

    const statusSelect = rendered.container.querySelectorAll("select")[0];
    choose(statusSelect, "review");
    await flush();

    expect(client.listMemories).toHaveBeenLastCalledWith({
      status: "review",
      limit: 20,
      offset: 0,
    });
  });

  it("uses the Needs review shortcut", async () => {
    const rendered = renderView();
    await flush();

    await clickAsync(buttonExact(rendered.container, "Needs review"));

    expect(client.listMemories).toHaveBeenLastCalledWith({
      status: "review",
      limit: 20,
      offset: 0,
    });
  });

  it("flags a memory whose source no longer exists", async () => {
    vi.mocked(client.listMemories).mockResolvedValue({
      items: [
        summary({
          provenance: { conversation_id: null, message_ids: [], valid: false },
        }),
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });

    const rendered = renderView();
    await flush();

    expect(textOf(rendered.container)).toContain("No valid source");
  });

  it("paginates with the API offset", async () => {
    const page = Array.from({ length: 20 }, (_, index) =>
      summary({ id: String(index).padStart(32, "0"), statement: `Memory ${index}` }),
    );
    vi.mocked(client.listMemories).mockImplementation(async (filters) => ({
      items: page,
      total: 45,
      limit: 20,
      offset: filters?.offset ?? 0,
    }));

    const rendered = renderView();
    await flush();
    expect(textOf(rendered.container)).toContain("Showing 1–20 of 45");

    await clickAsync(buttonExact(rendered.container, "Next"));

    expect(client.listMemories).toHaveBeenLastCalledWith({ limit: 20, offset: 20 });
    expect(textOf(rendered.container)).toContain("Showing 21–40 of 45");

    await clickAsync(buttonExact(rendered.container, "Previous"));
    expect(client.listMemories).toHaveBeenLastCalledWith({ limit: 20, offset: 0 });
  });

  it("shows a loading state before the first page arrives", async () => {
    vi.mocked(client.listMemories).mockImplementation(
      () => new Promise(() => undefined),
    );

    const rendered = renderView();

    expect(textOf(rendered.container)).toContain("Loading memories…");
  });

  it("distinguishes an empty store from an empty filter", async () => {
    vi.mocked(client.listMemories).mockResolvedValue({
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    });
    const rendered = renderView();
    await flush();
    expect(textOf(rendered.container)).toContain("No memories have been saved yet.");

    choose(rendered.container.querySelectorAll("select")[0], "review");
    await flush();
    expect(textOf(rendered.container)).toContain("No memories match these filters.");
  });

  it("shows a failure and retries the request", async () => {
    vi.mocked(client.listMemories)
      .mockRejectedValueOnce(new client.ApiRequestError("http_500", "Could not read memories."))
      .mockResolvedValue({ items: [summary()], total: 1, limit: 20, offset: 0 });

    const rendered = renderView();
    await flush();
    expect(textOf(rendered.container)).toContain("Could not read memories.");

    await clickAsync(buttonExact(rendered.container, "Try again"));

    expect(client.listMemories).toHaveBeenCalledTimes(2);
    expect(textOf(rendered.container)).toContain("You might like Oslo.");
  });
});

describe("memory detail", () => {
  it("shows provenance, history, origin, and confidence", async () => {
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    expect(client.getMemory).toHaveBeenCalledWith(MEMORY_ID);
    const text = textOf(rendered.container);
    expect(text).toContain("Sources");
    expect(text).toContain("Open source conversation");
    expect(text).toContain(MESSAGE_ID);
    expect(text).toContain("History");
    expect(text).toContain("Created");
    expect(text).toContain("0.50");
    expect(text).toContain("Inferred");
    expect(text).toContain("may be inaccurate");
    expect(text).toContain("not used in replies");
  });

  it("opens the source conversation only through the existing chat view", async () => {
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    click(buttonExact(rendered.container, "Open source conversation"));

    expect(onOpenConversation).toHaveBeenCalledWith(CONVERSATION_ID);
  });

  it("shows a detail failure and retries", async () => {
    vi.mocked(client.getMemory)
      .mockRejectedValueOnce(new client.ApiRequestError("http_404", "Memory not found."))
      .mockResolvedValue(detail());
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    expect(textOf(rendered.container)).toContain("Memory not found.");

    await clickAsync(buttonExact(rendered.container, "Try again"));
    expect(textOf(rendered.container)).toContain("Sources");
  });

  it("warns when the API reports invalid provenance", async () => {
    vi.mocked(client.getMemory).mockResolvedValue(
      detail({
        provenance: {
          conversation_id: CONVERSATION_ID,
          message_ids: [],
          valid: false,
        },
      }),
    );
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    expect(textOf(rendered.container)).toContain(
      "The original source is no longer available.",
    );
    expect(textOf(rendered.container)).toContain("excluded from retrieval");
  });

  it("does not offer invalid actions for an inactive memory", async () => {
    vi.mocked(client.getMemory).mockResolvedValue(detail({ status: "deleted" }));
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    const text = textOf(rendered.container);
    expect(text).toContain("soft delete");
    for (const label of ["Confirm memory", "Reject…", "Edit…", "Delete…"]) {
      expect(
        Array.from(rendered.container.querySelectorAll("button")).some((button) =>
          (button.textContent ?? "").includes(label),
        ),
      ).toBe(false);
    }
  });
});

describe("confirm and reject", () => {
  it("confirms a reviewable memory and refreshes the detail", async () => {
    vi.mocked(client.getMemory)
      .mockResolvedValueOnce(detail())
      .mockResolvedValue(detail({ status: "confirmed", last_confirmed_at: CREATED_AT }));
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    await clickAsync(buttonExact(rendered.container, "Confirm memory"));
    await flush();

    expect(client.confirmMemory).toHaveBeenCalledWith(MEMORY_ID);
    const text = textOf(rendered.container);
    expect(text).toContain("Memory confirmed.");
    expect(text).toContain("Confirmed");
    expect(client.getMemory).toHaveBeenCalledTimes(2);
  });

  it("rejects with an optional reason and explains the soft delete", async () => {
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    await clickAsync(buttonContaining(rendered.container, "Reject"));
    expect(textOf(rendered.container)).toContain("Reject this memory?");
    expect(textOf(rendered.container)).toContain("stored record and its history remain");

    setValue(
      rendered.container.querySelector<HTMLTextAreaElement>("#memory-reject-reason")!,
      "Not accurate",
    );
    await clickAsync(buttonExact(rendered.container, "Reject memory"));
    await flush();

    expect(client.rejectMemory).toHaveBeenCalledWith(MEMORY_ID, "Not accurate");
    expect(textOf(rendered.container)).toContain("Memory rejected and marked inactive.");
  });

  it("handles a 409 without showing success", async () => {
    vi.mocked(client.confirmMemory).mockRejectedValue(
      new client.ApiRequestError(
        "http_409",
        "Only accepted or review-pending memories can be confirmed.",
      ),
    );
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    await clickAsync(buttonExact(rendered.container, "Confirm memory"));

    const text = textOf(rendered.container);
    expect(text).toContain("Only accepted or review-pending memories can be confirmed.");
    expect(text).not.toContain("Memory confirmed.");
  });

  it("marks an accepted memory outdated with an optional reason", async () => {
    vi.mocked(client.getMemory)
      .mockResolvedValueOnce(detail({ status: "accepted", origin: "user_stated" }))
      .mockResolvedValue(detail({ status: "superseded", origin: "user_stated" }));
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    await clickAsync(buttonContaining(rendered.container, "Mark outdated"));
    const panelText = textOf(rendered.container);
    expect(panelText).toContain("Mark this memory outdated?");
    expect(panelText).toContain("excluded from replies");
    expect(panelText).toContain("history is retained");

    setValue(
      rendered.container.querySelector<HTMLTextAreaElement>("#memory-supersede-reason")!,
      "The user moved to Bergen.",
    );
    await clickAsync(buttonExact(rendered.container, "Mark outdated"));
    await flush();

    expect(client.supersedeMemory).toHaveBeenCalledWith(
      MEMORY_ID,
      "The user moved to Bergen.",
    );
    expect(textOf(rendered.container)).toContain(
      "Memory marked outdated and excluded from replies.",
    );
  });

  it("does not offer to mark a review candidate outdated", async () => {
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    expect(
      Array.from(rendered.container.querySelectorAll("button")).some((button) =>
        (button.textContent ?? "").includes("Mark outdated"),
      ),
    ).toBe(false);
  });

  it("prevents duplicate confirm submissions", async () => {
    let resolveConfirm: ((value: MemorySummary) => void) | undefined;
    vi.mocked(client.confirmMemory).mockImplementation(
      () =>
        new Promise<MemorySummary>((resolve) => {
          resolveConfirm = resolve;
        }),
    );
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    const first = buttonExact(rendered.container, "Confirm memory");
    await clickAsync(first);
    expect(client.confirmMemory).toHaveBeenCalledTimes(1);
    expect(first.disabled).toBe(true);

    await clickAsync(first);
    expect(client.confirmMemory).toHaveBeenCalledTimes(1);

    resolveConfirm?.(summary({ status: "confirmed" }));
    await flush();
  });
});

describe("corrections", () => {
  it("validates required content before calling the API", async () => {
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    await clickAsync(buttonContaining(rendered.container, "Edit"));
    setValue(rendered.container.querySelector<HTMLTextAreaElement>("#memory-statement")!, "   ");
    await clickAsync(buttonExact(rendered.container, "Save changes"));

    expect(client.correctMemory).not.toHaveBeenCalled();
    expect(textOf(rendered.container)).toContain("Statement cannot be empty.");
  });

  it("saves only the changed fields and shows the updated result", async () => {
    vi.mocked(client.getMemory)
      .mockResolvedValueOnce(detail())
      .mockResolvedValue(
        detail({ statement: "You like Bergen.", subject: "Bergen", status: "accepted" }),
      );
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    await clickAsync(buttonContaining(rendered.container, "Edit"));
    setValue(
      rendered.container.querySelector<HTMLTextAreaElement>("#memory-statement")!,
      "You like Bergen.",
    );
    setValue(rendered.container.querySelector<HTMLInputElement>("#memory-subject")!, "Bergen");
    await clickAsync(buttonExact(rendered.container, "Save changes"));
    await flush();

    expect(client.correctMemory).toHaveBeenCalledWith(MEMORY_ID, {
      statement: "You like Bergen.",
      subject: "Bergen",
    });
    const text = textOf(rendered.container);
    expect(text).toContain("Memory updated.");
    expect(text).toContain("You like Bergen.");
  });

  it("keeps the draft when the server rejects a correction", async () => {
    vi.mocked(client.correctMemory).mockRejectedValue(
      new client.ApiRequestError("http_422", "Statement cannot be empty."),
    );
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    await clickAsync(buttonContaining(rendered.container, "Edit"));
    const statement = rendered.container.querySelector<HTMLTextAreaElement>("#memory-statement")!;
    setValue(statement, "A new draft");
    await clickAsync(buttonExact(rendered.container, "Save changes"));
    await flush();

    expect(textOf(rendered.container)).toContain("Statement cannot be empty.");
    expect(
      rendered.container.querySelector<HTMLTextAreaElement>("#memory-statement")?.value,
    ).toBe("A new draft");
  });
});

describe("deletion and keyboard behavior", () => {
  it("requires confirmation and states the soft-delete policy", async () => {
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    await clickAsync(buttonContaining(rendered.container, "Delete"));
    const panelText = textOf(rendered.container);
    expect(panelText).toContain("Delete this memory?");
    expect(panelText).toContain("soft delete");
    expect(panelText).toContain("Source conversations and messages are not deleted.");

    await clickAsync(buttonExact(rendered.container, "Delete memory"));
    await flush();

    expect(client.deleteMemory).toHaveBeenCalledWith(MEMORY_ID);
    expect(textOf(rendered.container)).toContain("Select a memory to inspect it.");
  });

  it("cancels a confirmation with Escape", async () => {
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    await clickAsync(buttonContaining(rendered.container, "Reject"));
    const panel = rendered.container.querySelector(".memory-confirm");
    expect(panel).not.toBeNull();
    if (!panel) throw new Error("confirmation panel is missing");

    pressEscape(panel);

    expect(textOf(rendered.container)).not.toContain("Reject this memory?");
    expect(client.rejectMemory).not.toHaveBeenCalled();
  });

  it("moves focus to the safe cancel action when a destructive panel opens", async () => {
    const rendered = renderView();
    await flush();
    await openMemory(rendered.container);

    await clickAsync(buttonContaining(rendered.container, "Delete"));

    expect(document.activeElement?.textContent).toContain("Cancel");
  });
});
