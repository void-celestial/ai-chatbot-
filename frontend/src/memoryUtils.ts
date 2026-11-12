import type {
  MemoryHistoryValue,
  MemoryKind,
  MemoryOrigin,
  MemoryStatus,
} from "./types";

export const MEMORY_KIND_LABELS: Record<MemoryKind, string> = {
  fact: "Fact",
  preference: "Preference",
  goal: "Goal",
  event: "Event",
  person: "Person",
  other: "Other",
};

export const MEMORY_ORIGIN_LABELS: Record<MemoryOrigin, string> = {
  user_stated: "Stated by you",
  inferred: "Inferred",
};

export const MEMORY_STATUS_LABELS: Record<MemoryStatus, string> = {
  accepted: "Active",
  review: "Needs review",
  confirmed: "Confirmed",
  conflicted: "Conflicted",
  superseded: "Superseded",
  orphaned: "Source missing",
  deleted: "Inactive",
};

export function kindLabel(value: string): string {
  return MEMORY_KIND_LABELS[value as MemoryKind] ?? value;
}

export function statusLabel(value: string): string {
  return MEMORY_STATUS_LABELS[value as MemoryStatus] ?? value;
}

export function originLabel(value: string): string {
  return MEMORY_ORIGIN_LABELS[value as MemoryOrigin] ?? value;
}

// Mirrors the backend policy in memory_service.py. The UI must not offer an
// action the API will reject with 409.
export function canConfirmMemory(status: string): boolean {
  return status === "review" || status === "accepted";
}

export function canRejectMemory(status: string): boolean {
  return status === "review";
}

// Marking a memory outdated is only meaningful for active, trusted records.
// Review candidates are rejected instead, and terminal statuses stay terminal.
export function canSupersedeMemory(status: string): boolean {
  return status === "accepted" || status === "confirmed";
}

export function canEditMemory(status: string): boolean {
  return status === "accepted" || status === "review" || status === "confirmed";
}

export function canDeleteMemory(status: string): boolean {
  return status !== "deleted";
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return "Something went wrong. Please try again.";
}

export interface HistoryDescription {
  statusChange?: string;
  reason?: string;
  before?: string;
  after?: string;
}

export function describeHistory(value: MemoryHistoryValue): HistoryDescription {
  const description: HistoryDescription = {};
  if (value.status_from && value.status_to) {
    description.statusChange = `Status changed from ${statusLabel(value.status_from)} to ${statusLabel(
      value.status_to,
    )}.`;
  } else if (value.status_to) {
    description.statusChange = `Status set to ${statusLabel(value.status_to)}.`;
  }
  if (typeof value.reason === "string" && value.reason.trim()) {
    description.reason = value.reason.trim();
  }
  if (value.before?.statement && value.after?.statement && value.before.statement !== value.after.statement) {
    description.before = value.before.statement;
    description.after = value.after.statement;
  }
  return description;
}

export const MEMORY_FILTER_LIMIT = 20;
