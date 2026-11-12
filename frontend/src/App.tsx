import { useEffect, useState } from "react";
import { CompanionEmblem } from "./components/CompanionEmblem";
import { ErrorBanner } from "./components/ErrorBanner";
import { MemoryView } from "./components/MemoryView";
import { MessageInput } from "./components/MessageInput";
import { MessageList } from "./components/MessageList";
import { Sidebar } from "./components/Sidebar";
import { VoiceNotesView } from "./components/VoiceNotesView";
import { exportData, fetchHealth } from "./api/client";
import { useCompanion } from "./hooks/useCompanion";
import { useTheme } from "./hooks/useTheme";
import type { AppView, HealthStatus } from "./types";

export default function App() {
  const { theme, toggleTheme } = useTheme();
  const {
    conversations,
    activeConversation,
    activeId,
    messages,
    streamedReply,
    isGenerating,
    isLoadingMessages,
    isLoadingConversations,
    conversationsError,
    messagesError,
    retryLoadMessages,
    isDeletingConversation,
    backendError,
    canRetry,
    refreshConversations,
    retryLoadConversations,
    selectConversation,
    deleteConversation,
    newConversation,
    sendMessage,
    retryGeneration,
    stopGeneration,
    dismissError,
  } = useCompanion();

  const [health, setHealth] = useState<HealthStatus | null>(null);
  // Kept apart from `health` deliberately: a failed health request means the
  // backend is down, which is not the same thing as Ollama being down. The
  // last known health is kept so the sidebar does not start claiming a model
  // problem that was never observed.
  const [isBackendUnreachable, setIsBackendUnreachable] = useState(false);
  const [view, setView] = useState<AppView>("chat");
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await fetchHealth();
        if (!cancelled) {
          setHealth(status);
          setIsBackendUnreachable(false);
        }
      } catch {
        // The backend never answered: report that, rather than pinning the
        // blame on Ollama or looping on "Checking Ollama…".
        if (!cancelled) setIsBackendUnreachable(true);
      }
    };
    void poll();
    const interval = window.setInterval(poll, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    void refreshConversations(true);
  }, [refreshConversations]);

  const handleDeleteConversation = async () => {
    if (!activeId || isDeletingConversation || isGenerating) return;
    const deleted = await deleteConversation(activeId);
    if (deleted) setIsDeleteOpen(false);
  };

  const handleExportData = async () => {
    if (isExporting) return;
    setIsExporting(true);
    setExportError(null);
    try {
      const payload = await exportData();
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `companion-export-${payload.exported_at.slice(0, 10)}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setExportError(
        error instanceof Error && error.message.trim()
          ? error.message
          : "Could not export local data.",
      );
    } finally {
      setIsExporting(false);
    }
  };

  const openView = (next: AppView) => {
    setIsDeleteOpen(false);
    setIsSidebarOpen(false);
    setView(next);
  };

  return (
    <div className={`app-shell${isSidebarOpen ? " sidebar-open" : ""}`}>
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        isLoadingConversations={isLoadingConversations}
        conversationsError={conversationsError}
        onRetryConversations={retryLoadConversations}
        health={health}
        theme={theme}
        view={view}
        onNewConversation={() => {
          openView("chat");
          void newConversation();
        }}
        onSelect={(conversationId) => {
          openView("chat");
          selectConversation(conversationId);
        }}
        onOpenMemories={() => openView("memories")}
        onOpenVoiceNotes={() => openView("voice")}
        onExportData={() => void handleExportData()}
        isExporting={isExporting}
        dataError={exportError}
        onToggleTheme={toggleTheme}
      />

      {isSidebarOpen && (
        <button
          type="button"
          className="sidebar-scrim"
          aria-label="Close sidebar"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <div className="chat-stage">
        {view === "memories" ? (
          <MemoryView
            conversations={conversations}
            onBackToChat={() => openView("chat")}
            onOpenConversation={(conversationId) => {
              openView("chat");
              selectConversation(conversationId);
            }}
          />
        ) : view === "voice" ? (
          <VoiceNotesView
            onBackToChat={() => openView("chat")}
            onOpenConversation={(conversationId) => {
              openView("chat");
              selectConversation(conversationId);
            }}
          />
        ) : (
          <main className="chat-pane">
            <a className="skip-link" href="#conversation">
              Skip to the conversation
            </a>

            <header className="chat-header">
              <div className="chat-header-row">
                <button
                  type="button"
                  className="btn btn-ghost sidebar-toggle"
                  onClick={() => setIsSidebarOpen((open) => !open)}
                  aria-label="Toggle sidebar"
                  aria-expanded={isSidebarOpen}
                >
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    aria-hidden="true"
                    focusable="false"
                  >
                    <path
                      d="M4 7h16M4 12h16M4 17h10"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                    />
                  </svg>
                </button>

                <div className="chat-identity">
                  <CompanionEmblem size={38} />
                  <div className="chat-heading">
                    <h2 className="chat-title">
                      {activeConversation ? activeConversation.title : "Companion"}
                    </h2>
                    <p className="chat-subtitle">
                      {isGenerating ? "Writing a reply…" : "Private · running on this device"}
                    </p>
                  </div>
                </div>

                {activeConversation && (
                  <button
                    type="button"
                    className="btn btn-ghost chat-delete-button"
                    onClick={() => setIsDeleteOpen(true)}
                    disabled={isGenerating || isDeletingConversation}
                    aria-label="Delete this conversation"
                  >
                    Delete…
                  </button>
                )}
              </div>
            </header>

            {isDeleteOpen && activeConversation && (
              <section
                className="chat-delete-panel"
                aria-labelledby="delete-conversation-heading"
                onKeyDown={(event) => {
                  if (event.key === "Escape" && !isDeletingConversation) setIsDeleteOpen(false);
                }}
              >
                <h3 id="delete-conversation-heading">Delete this conversation?</h3>
                <p>
                  This permanently removes <strong>{activeConversation.title}</strong> and its
                  messages from the local database. Memories derived from it are kept, but they are
                  marked <strong>Source missing</strong> and excluded from replies. Migration backups
                  may still contain earlier copies, so this is not complete erasure. Export your data
                  first if you want a copy.
                </p>
                <div className="memory-actions">
                  <button
                    type="button"
                    className="btn btn-danger"
                    disabled={isDeletingConversation || isGenerating}
                    onClick={() => void handleDeleteConversation()}
                  >
                    {isDeletingConversation ? "Deleting…" : "Delete conversation"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={isDeletingConversation}
                    onClick={() => setIsDeleteOpen(false)}
                    autoFocus
                  >
                    Cancel
                  </button>
                </div>
              </section>
            )}

            {isBackendUnreachable && !backendError && (
              <div className="error-banner" role="alert">
                <div className="error-text">
                  <strong>Backend offline:</strong> The assistant backend is not running.
                  Restart the application. This notice clears itself once the backend
                  answers again.
                </div>
              </div>
            )}

            {backendError && (
              <ErrorBanner
                error={backendError}
                canRetry={canRetry}
                isRetrying={isGenerating}
                onRetry={() => void retryGeneration()}
                onDismiss={dismissError}
              />
            )}

            <div className="chat-scroll" id="conversation" tabIndex={-1}>
              <MessageList
                messages={messages}
                streamedContent={streamedReply?.content ?? null}
                isLoading={isLoadingMessages}
                conversationTitle={activeConversation?.title ?? ""}
                errorMessage={messagesError}
                onRetry={retryLoadMessages}
              />
            </div>

            <div className="composer-dock">
              <MessageInput
                disabled={!activeId}
                isGenerating={isGenerating}
                onSend={(content) => void sendMessage(content)}
                onStop={stopGeneration}
              />
            </div>
          </main>
        )}
      </div>
    </div>
  );
}
