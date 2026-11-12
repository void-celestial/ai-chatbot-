import type { AppView, Conversation, HealthStatus } from "../types";
import { formatTimestamp } from "../types";
import { CompanionEmblem } from "./CompanionEmblem";
import { StatusBadge } from "./StatusBadge";

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  isLoadingConversations: boolean;
  conversationsError: string | null;
  onRetryConversations: () => void;
  health: HealthStatus | null;
  theme: "light" | "dark";
  view: AppView;
  onNewConversation: () => void;
  onSelect: (conversationId: string) => void;
  onOpenMemories: () => void;
  onOpenVoiceNotes: () => void;
  onExportData: () => void;
  isExporting: boolean;
  dataError: string | null;
  onToggleTheme: () => void;
}

const ICONS = {
  plus: "M12 5.5v13M5.5 12h13",
  memories:
    "M12 3.8 4.8 7.3v9.4L12 20.2l7.2-3.5V7.3L12 3.8ZM4.9 7.4 12 10.9l7.1-3.5M12 10.9v9.2",
  voice: "M12 4.6a3.1 3.1 0 0 1 3.1 3.1v3.9a3.1 3.1 0 0 1-6.2 0V7.7A3.1 3.1 0 0 1 12 4.6Z M6.4 11a5.6 5.6 0 0 0 11.2 0M12 16.6v2.9",
  export: "M12 15.3V4.9M8.4 8.5 12 4.9l3.6 3.6M5.6 15.1v2.8a1.7 1.7 0 0 0 1.7 1.7h9.4a1.7 1.7 0 0 0 1.7-1.7v-2.8",
} as const;

function Icon({ path }: { path: string }) {
  return (
    <svg className="nav-icon" width="17" height="17" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d={path}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Sidebar({
  conversations,
  activeId,
  isLoadingConversations,
  conversationsError,
  onRetryConversations,
  health,
  theme,
  view,
  onNewConversation,
  onSelect,
  onOpenMemories,
  onOpenVoiceNotes,
  onExportData,
  isExporting,
  dataError,
  onToggleTheme,
}: Props) {
  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="brand">
          <CompanionEmblem size={36} />
          <div className="brand-copy">
            <h1 className="app-title brand-name">My AI Assistant</h1>
            <span className="brand-tagline">Local · offline</span>
          </div>
        </div>
        <button
          type="button"
          className="btn btn-ghost theme-toggle"
          onClick={onToggleTheme}
          title="Switch light/dark theme"
          aria-label="Switch light/dark theme"
        >
          <span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
        </button>
      </div>

      <button type="button" className="btn btn-primary new-chat" onClick={onNewConversation}>
        <Icon path={ICONS.plus} />
        <span>New conversation</span>
      </button>

      <nav className="sidebar-nav" aria-label="Workspace">
        <button
          type="button"
          className={`nav-item${view === "memories" ? " active" : ""}`}
          onClick={onOpenMemories}
          aria-current={view === "memories" ? "page" : undefined}
        >
          <Icon path={ICONS.memories} />
          <span>Memories</span>
        </button>

        <button
          type="button"
          className={`nav-item${view === "voice" ? " active" : ""}`}
          onClick={onOpenVoiceNotes}
          aria-current={view === "voice" ? "page" : undefined}
        >
          <Icon path={ICONS.voice} />
          <span>Voice notes</span>
        </button>

        <button
          type="button"
          className="nav-item"
          onClick={onExportData}
          disabled={isExporting}
        >
          <Icon path={ICONS.export} />
          <span>{isExporting ? "Exporting…" : "Export data"}</span>
        </button>
      </nav>

      {dataError && (
        <p className="sidebar-data-error" role="alert">
          {dataError}
        </p>
      )}

      <div className="sidebar-status">
        <StatusBadge health={health} />
      </div>

      <p className="sidebar-section-label">Conversations</p>

      <nav className="conversation-list" aria-label="Conversations">
        {isLoadingConversations ? (
          <p className="sidebar-empty" role="status">
            Loading conversations…
          </p>
        ) : conversationsError ? (
          <div className="sidebar-load-error" role="alert">
            <p className="sidebar-load-error-text">{conversationsError}</p>
            <button type="button" className="btn btn-secondary btn-sm" onClick={onRetryConversations}>
              Try again
            </button>
          </div>
        ) : conversations.length === 0 ? (
          <p className="sidebar-empty">
            No conversations yet. Start one above, or just say hello below.
          </p>
        ) : (
          conversations.map((conversation) => (
            <button
              key={conversation.id}
              type="button"
              className={`conversation-item${conversation.id === activeId ? " active" : ""}`}
              onClick={() => onSelect(conversation.id)}
              title={conversation.title}
              aria-current={conversation.id === activeId ? "true" : undefined}
            >
              <span className="conversation-title">{conversation.title}</span>
              <span className="conversation-meta">
                {formatTimestamp(conversation.updated_at)} · {conversation.message_count}{" "}
                {conversation.message_count === 1 ? "msg" : "msgs"}
              </span>
            </button>
          ))
        )}
      </nav>
    </aside>
  );
}
